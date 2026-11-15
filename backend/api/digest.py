"""
API — Digest
GET /api/digest?window=7d&group_by=priority&symbol=&category=
Returns tiered, CLUSTERED announcements with score breakdowns.

- Window is anchored to data_as_of (demo clock): "24h" = the 24h ending at
  the end of the latest data day, not the real current time.
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


def _parse_window(window: str) -> datetime.timedelta:
    """Convert '24h', '3d', '7d' to a window length."""
    m = re.match(r"(\d+)([hd])", window)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return datetime.timedelta(hours=n) if unit == "h" else datetime.timedelta(days=n)
    return datetime.timedelta(days=1)


# Bodies that mention results — such filings must never show a bare subject.
RESULTS_BODY_RE = re.compile(
    r"financial results|results for the (?:quarter|half year|year|period)|"
    r"(?:quarter|half year|year) ended|\bresults\b.*\b(?:quarter|fiscal|fy)\b",
    re.I,
)


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


# Absolute floor for must-see, picked from the data: results clusters score
# 10-12, senior management changes 10-12, real fund raises 7-9.8; routine
# paperwork (ESOP allotments, compliance certificates, re-appointments) sits
# below 4. 9.0 admits only the top material band, independent of window size.
MUST_SEE_FLOOR = 9.0


def _assign_tiers(clusters: list[dict]) -> None:
    """Relative ranking within the window. Mutates cluster['tier'].
    must_see = top 15% of clusters AND score >= MUST_SEE_FLOOR (a window may
    have zero must-see items). worth_a_look = next 25%."""
    ranked = sorted(
        clusters,
        key=lambda c: (
            -(c["lead"]["score"] or 0),
            -c["lead"]["breakdown"].get("category_base", 0),
            c["lead"]["ts_iso"] or "",
        ),
    )
    n = len(ranked)
    n_must = min(10, -(-n * 15 // 100), n) if n else 0  # ceil(15%), max 10, NO min
    n_worth = -(-n * 25 // 100)  # next 25%

    must_by_sym: dict[str, int] = {}
    must_left, worth_left = n_must, n_worth
    for c in ranked:
        sym = c["symbol"]
        # No content (no voice summary) -> never must-see/worth-a-look.
        has_content = bool((c["lead"].get("voice_summary") or "").strip())
        if (
            must_left > 0
            and must_by_sym.get(sym, 0) < 3
            and (c["lead"]["score"] or 0) >= MUST_SEE_FLOOR
            and has_content
        ):
            c["tier"] = "must_see"
            must_by_sym[sym] = must_by_sym.get(sym, 0) + 1
            must_left -= 1
        elif worth_left > 0 and has_content:
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Demo clock: anchor the window to data_as_of, not real now.
    as_of = conn.execute("SELECT MAX(ts_iso) FROM announcements").fetchone()[0]
    last_price = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    if last_price and (not as_of or last_price > as_of[:10]):
        as_of = last_price
    as_of_day = (as_of or datetime.date.today().isoformat())[:10]
    anchor_end = datetime.datetime.fromisoformat(as_of_day + "T23:59:59")
    cutoff = anchor_end - _parse_window(window)

    query = """
        SELECT a.id, a.symbol, a.ts, a.ts_iso, a.subject, a.voice_summary,
               a.raw_desc, a.attachment_url, a.category, a.priority_tier,
               a.priority_score, a.score_breakdown
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

    clusters = _cluster(rows)
    for c in clusters:
        lead = c["children"][0]
        c["lead"] = {
            "id": lead["id"],
            "ts": lead["ts"],
            "ts_iso": lead["ts_iso"],
            "subject": lead["subject"],
            "voice_summary": lead["voice_summary"],
            "raw_desc": lead["raw_desc"],
            "attachment_url": lead["attachment_url"],
            "score": lead["priority_score"],
            "breakdown": json.loads(lead["score_breakdown"] or "{}"),
            "price_ctx": price_context(lead["symbol"], lead["ts"], conn),
        }
    _assign_tiers(clusters)

    must_see, worth_a_look, routine = [], [], []

    def cluster_payload(c: dict) -> dict:
        lead = c["lead"]
        vs = (lead["voice_summary"] or "").strip()
        mentions_results = c["category"] != "results" and bool(RESULTS_BODY_RE.search(vs or lead["subject"] or ""))
        if c["category"] == "results":
            headline = vs or "Financial results filed"
        elif mentions_results:
            headline = "Financial results filed — see results card"
        else:
            headline = vs or lead["subject"]
        rs = None
        if c["category"] == "results":
            from backend.push.results_summary import results_summary
            try:
                rs = results_summary(
                    c["symbol"], lead["ts_iso"] or "",
                    [lead["voice_summary"] or "", lead.get("raw_desc") or "", lead["subject"] or ""],
                    conn,
                )
            except Exception:
                rs = None
        mgmt = None
        if c["category"] == "management_change":
            from backend.push.people import extract_mgmt_change
            try:
                mc = extract_mgmt_change(
                    lead["subject"] or "", lead["voice_summary"] or "",
                    lead.get("raw_desc") or "",
                )
                if mc:
                    mgmt = mc
            except Exception:
                mgmt = None
            # batch enrichment (cached in DB by scripts/enrich_people.py) overlays the regex
            er = conn.execute(
                "SELECT * FROM people_enrichment WHERE announcement_id=?", (lead["id"],)
            ).fetchone()
            if er:
                base = mgmt or {}
                mgmt = {
                    "person": er["person"] or base.get("person"),
                    "role": er["designation"] or base.get("role"),
                    "action": er["action"] or base.get("action") or "change",
                    "effective_date": er["effective_date"] or base.get("effective_date"),
                    "reason": er["reason"],
                    "predecessor": er["predecessor"],
                    "bio": er["bio"],
                    "confidence": er["confidence"],
                    "sources": [
                        s if isinstance(s, dict) else {"title": None, "url": s, "snippet": None}
                        for s in json.loads(er["sources_json"] or "[]")
                    ],
                }
        return {
            "id": lead["id"],
            "symbol": c["symbol"],
            "ts": lead["ts"],
            "ts_iso": lead["ts_iso"],
            "subject": lead["subject"],
            "voice_summary": lead["voice_summary"],
            "headline": headline,
            "mentions_results": mentions_results,
            "results_summary": rs,
            "mgmt_change": mgmt,
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
        "anchor_end": anchor_end.isoformat(),
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
