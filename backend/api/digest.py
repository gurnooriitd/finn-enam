"""
API — Digest
GET /api/digest?window=7d&group_by=priority&symbol=&category=
Returns tiered, CLUSTERED announcements with score breakdowns.

- Window is anchored to the real current time (not the latest data date).
- Filings of the same company + category within 24h collapse into one
  cluster led by its highest-scored child.
- Tiers are assigned by relative rank within the window:
  must_see = top 15% of clusters (min 3, max 10), worth_a_look = next 25%,
  routine = rest. Ties break by category base, then recency.
  At most 3 must-see clusters per company.
- data_as_of = latest announcement/price date actually in the DB.
"""
import json
import sqlite3
import datetime
import re

from fastapi import APIRouter, Query

from backend.config import DB_PATH
from backend.push.context import price_context

router = APIRouter()


def _parse_window(window: str) -> datetime.datetime:
    """Convert '24h', '3d', '7d' to a cutoff datetime anchored to now."""
    m = re.match(r"(\d+)([hd])", window)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = datetime.timedelta(hours=n) if unit == "h" else datetime.timedelta(days=n)
        return datetime.datetime.now() - delta
    return datetime.datetime.now() - datetime.timedelta(days=1)


def _cluster(rows: list) -> list[dict]:
    """Group consecutive rows (same symbol+category, gap <= 24h) into clusters.
    Lead = highest-scored child (ties: category base, then recency)."""
    clusters: list[dict] = []
    for r in rows:
        placed = False
        for c in clusters:
            if c["symbol"] == r["symbol"] and c["category"] == r["category"]:
                try:
                    first = datetime.datetime.fromisoformat(c["children"][0]["ts_iso"])
                    cur = datetime.datetime.fromisoformat(r["ts_iso"])
                    if (cur - first) <= datetime.timedelta(hours=24):
                        c["children"].append(r)
                        placed = True
                except (ValueError, TypeError):
                    pass
                break
        if not placed:
            clusters.append({"symbol": r["symbol"], "category": r["category"], "children": [r]})

    # Lead child per cluster: score desc, category_base desc, recency desc
    for c in clusters:
        c["children"].sort(
            key=lambda r: (
                -(r["priority_score"] or 0),
                -(json.loads(r["score_breakdown"] or "{}").get("category_base", 0) or 0),
                r["ts_iso"] or "",
            )
        )
    return clusters


def _assign_tiers(clusters: list[dict]) -> None:
    """Relative ranking within the window. Mutates cluster['tier']."""
    ranked = sorted(
        clusters,
        key=lambda c: (
            -(c["lead"]["score"] or 0),
            -c["lead"]["breakdown"].get("category_base", 0),
            c["lead"]["ts_iso"] or "",
        ),
    )
    n = len(ranked)
    n_must = min(10, max(3, -(-n * 15 // 100)), n) if n else 0  # ceil(15%), min 3, max 10
    n_worth = -(-n * 25 // 100)  # next 25%

    must_by_sym: dict[str, int] = {}
    must_left, worth_left = n_must, n_worth
    for c in ranked:
        sym = c["symbol"]
        if must_left > 0 and must_by_sym.get(sym, 0) < 3:
            c["tier"] = "must_see"
            must_by_sym[sym] = must_by_sym.get(sym, 0) + 1
            must_left -= 1
        elif worth_left > 0:
            c["tier"] = "worth_a_look"
            worth_left -= 1
        else:
            c["tier"] = "routine"


@router.get("/digest")
def get_digest(
    window: str = Query("7d", description="e.g. 24h, 3d, 7d"),
    symbol: str = Query("", description="filter by ticker"),
    category: str = Query("", description="comma-separated categories"),
    portfolio_only: bool = Query(False),
    group_by: str = Query("priority", description="priority | company"),
):
    cutoff = _parse_window(window)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT a.id, a.symbol, a.ts, a.ts_iso, a.subject, a.voice_summary,
               a.attachment_url, a.category, a.priority_tier, a.priority_score,
               a.score_breakdown
        FROM announcements a
        WHERE a.ts_iso >= ?
    """
    params: list = [cutoff.isoformat(sep=" ")]

    if symbol:
        query += " AND a.symbol = ?"
        params.append(symbol.upper())
    if category:
        cats = [c.strip() for c in category.split(",")]
        query += f" AND a.category IN ({','.join('?' * len(cats))})"
        params.extend(cats)
    if portfolio_only:
        from backend.config import PORTFOLIO_TICKERS
        query += f" AND a.symbol IN ({','.join('?' * len(PORTFOLIO_TICKERS))})"
        params.extend(PORTFOLIO_TICKERS)

    query += " ORDER BY a.ts_iso ASC"
    rows = conn.execute(query, params).fetchall()

    # data_as_of: latest announcement / price timestamp in the DB
    as_of = conn.execute("SELECT MAX(ts_iso) FROM announcements").fetchone()[0]
    last_price = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    if last_price and (not as_of or last_price > as_of[:10]):
        as_of = last_price

    clusters = _cluster(rows)
    for c in clusters:
        lead = c["children"][0]
        c["lead"] = {
            "id": lead["id"],
            "ts": lead["ts"],
            "ts_iso": lead["ts_iso"],
            "subject": lead["subject"],
            "voice_summary": lead["voice_summary"],
            "attachment_url": lead["attachment_url"],
            "score": lead["priority_score"],
            "breakdown": json.loads(lead["score_breakdown"] or "{}"),
            "price_ctx": price_context(lead["symbol"], lead["ts"], conn),
        }
    _assign_tiers(clusters)

    must_see, worth_a_look, routine = [], [], []

    def cluster_payload(c: dict) -> dict:
        lead = c["lead"]
        return {
            "id": lead["id"],
            "symbol": c["symbol"],
            "ts": lead["ts"],
            "ts_iso": lead["ts_iso"],
            "subject": lead["subject"],
            "voice_summary": lead["voice_summary"],
            "attachment_url": lead["attachment_url"],
            "category": c["category"],
            "score": lead["score"],
            "tier": c["tier"],
            "score_breakdown": lead["breakdown"],
            "child_count": len(c["children"]),
            "price_ctx": lead["price_ctx"],
            "children": [
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "ts_iso": r["ts_iso"],
                    "subject": r["subject"],
                    "voice_summary": r["voice_summary"],
                    "attachment_url": r["attachment_url"],
                    "score": r["priority_score"],
                    "price_ctx": price_context(r["symbol"], r["ts"], conn),
                }
                for r in c["children"][1:]
            ],
        }

    for c in clusters:
        item = cluster_payload(c)
        if c["tier"] == "must_see":
            must_see.append(item)
        elif c["tier"] == "worth_a_look":
            worth_a_look.append(item)
        else:
            routine.append(item)

    conn.close()

    # Lanes ranked: score desc, category base desc, recency desc
    def _ranked(items: list) -> list:
        return sorted(
            items,
            key=lambda c: (
                -(c["score"] or 0),
                -c["score_breakdown"].get("category_base", 0),
                c["ts_iso"] or "",
            ),
        )

    must_see, worth_a_look = _ranked(must_see), _ranked(worth_a_look)

    return {
        "window": window,
        "cutoff": cutoff.isoformat(),
        "data_as_of": as_of,
        "total": len(rows),
        "summary": {
            "must_see": len(must_see),
            "worth_a_look": len(worth_a_look),
            "routine": len(routine),
        },
        "must_see": must_see,
        "worth_a_look": worth_a_look,
        "routine": {"count": len(routine), "items": routine},
    }
