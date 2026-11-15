"""
API — Digest
GET /api/digest?window=7d&group_by=priority&symbol=&category=
Returns tiered announcements with score breakdowns.
"""
import json
import sqlite3
import datetime
import re
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.config import DB_PATH

router = APIRouter()


def _parse_window(window: str) -> datetime.date:
    """Convert '24h', '3d', '7d', 'custom' to a cutoff date."""
    m = re.match(r"(\d+)([hd])", window)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = datetime.timedelta(hours=n) if unit == "h" else datetime.timedelta(days=n)
        return (datetime.datetime.utcnow() - delta).date()
    return datetime.date.today() - datetime.timedelta(days=1)


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
        SELECT a.id, a.symbol, a.ts, a.subject, a.voice_summary, a.attachment_url,
               a.category, a.priority_tier, a.priority_score, a.score_breakdown,
               p.close as price, p.volume as vol
        FROM announcements a
        LEFT JOIN (
            SELECT symbol, close, volume, date
            FROM prices
            WHERE (symbol, date) IN (
                SELECT symbol, MAX(date) FROM prices GROUP BY symbol
            )
        ) p ON a.symbol = p.symbol
        WHERE a.ts >= ?
    """
    params: list = [cutoff.isoformat()]

    if symbol:
        query += " AND a.symbol = ?"
        params.append(symbol.upper())
    if category:
        cats = [c.strip() for c in category.split(",")]
        query += f" AND a.category IN ({','.join('?'*len(cats))})"
        params.extend(cats)
    if portfolio_only:
        from backend.config import PORTFOLIO_TICKERS
        query += f" AND a.symbol IN ({','.join('?'*len(PORTFOLIO_TICKERS))})"
        params.extend(PORTFOLIO_TICKERS)

    query += " ORDER BY COALESCE(a.priority_score, 0) DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    must_see, worth_a_look, routine = [], [], []
    for r in rows:
        tier = r["priority_tier"] or "routine"
        breakdown = {}
        try:
            breakdown = json.loads(r["score_breakdown"] or "{}")
        except Exception:
            pass

        item = {
            "id": r["id"],
            "symbol": r["symbol"],
            "ts": r["ts"],
            "subject": r["subject"],
            "voice_summary": r["voice_summary"],
            "attachment_url": r["attachment_url"],
            "category": r["category"],
            "score": r["priority_score"],
            "tier": tier,
            "score_breakdown": breakdown,
            "price": r["price"],
            "volume": r["vol"],
        }

        if tier == "must_see":
            must_see.append(item)
        elif tier == "worth_a_look":
            worth_a_look.append(item)
        else:
            routine.append(item)

    return {
        "window": window,
        "cutoff": cutoff.isoformat(),
        "total": len(rows),
        "summary": {
            "must_see": len(must_see),
            "worth_a_look": len(worth_a_look),
            "routine": len(routine),
        },
        "must_see": must_see,
        "worth_a_look": worth_a_look,
        "routine": {
            "count": len(routine),
            "items": routine,
        },
    }
