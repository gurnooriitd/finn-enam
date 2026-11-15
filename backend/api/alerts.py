"""
API — Alerts
GET  /api/alerts/rules        list rules
POST /api/alerts/rules        create rule
PUT  /api/alerts/rules/{id}   update rule
GET  /api/alerts/events       list fired events
POST /api/alerts/scan         trigger scan now
GET  /api/alerts/dryrun       what would fire in last 30 days
"""
import json
import sqlite3
import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from backend.config import DB_PATH
from backend.push.context import company_name

router = APIRouter()

CAT_LABELS = {
    "results": "Results filed",
    "M&A": "M&A announced",
    "management_change": "Management change",
    "litigation": "Litigation update",
    "credit_rating": "Credit rating action",
    "fund_raise": "Fund raising",
    "dividend": "Dividend declared",
    "pledging": "Pledge update",
    "insider_trading": "Insider trade",
}


def _price_move(symbol: str, date: str, conn) -> float | None:
    rows = conn.execute(
        "SELECT close FROM prices WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 2",
        (symbol, date),
    ).fetchall()
    if len(rows) < 2 or not rows[1][0]:
        return None
    return round((rows[0][0] - rows[1][0]) / rows[1][0] * 100, 1)


def _on_results_day(symbol: str, date: str, conn) -> bool:
    """Results filed that day or the previous day (after-close filings)."""
    try:
        d = datetime.date.fromisoformat(date)
    except (ValueError, TypeError):
        return False
    for back in (0, 1):
        day = (d - datetime.timedelta(days=back)).isoformat()
        n = conn.execute(
            "SELECT COUNT(*) FROM announcements WHERE symbol=? AND category='results' "
            "AND substr(ts_iso,1,10)=?",
            (symbol, day),
        ).fetchone()[0]
        if n:
            return True
    return False


def _data_as_of(conn) -> str:
    """Demo clock: latest announcement or price date in the DB."""
    a = conn.execute("SELECT MAX(ts_iso) FROM announcements").fetchone()[0] or ""
    p = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0] or ""
    return max((a or "")[:10], (p or "")[:10])


def _enrich(row, conn) -> dict:
    e = dict(row)
    e["company"] = company_name(e["symbol"])
    kind = e["kind"]
    if kind == "volume_ratio":
        reason = f"Volume {e['value']}x 20-day avg"
    elif kind == "price_move":
        reason = f"Price {e['value']:+.1f}%"
    else:
        cat, summary, url = None, "", ""
        if e["announcement_id"]:
            r = conn.execute(
                "SELECT category, voice_summary, subject, attachment_url "
                "FROM announcements WHERE id=?",
                (e["announcement_id"],),
            ).fetchone()
            if r:
                cat = r[0]
                summary = (r[1] or r[2] or "").strip()
                url = r[3] or ""
        label = CAT_LABELS.get(cat or "", (cat or "Announcement filed").replace("_", " "))
        reason = f"{label}: {summary[:110]}" if summary else label
        e["summary"] = summary
        e["attachment_url"] = url
    if kind in ("volume_ratio", "price_move"):
        pm = _price_move(e["symbol"], e["date"], conn)
        if pm is not None and abs(pm) >= 1 and kind == "volume_ratio":
            reason += f", price {pm:+.1f}%"
        if _on_results_day(e["symbol"], e["date"], conn):
            reason += " (results filed)"
    e["reason"] = reason
    return e


def _recent_cutoff(conn) -> str:
    """Only events within 30 days of data_as_of (demo clock)."""
    try:
        d = datetime.date.fromisoformat(_data_as_of(conn)[:10])
    except (ValueError, TypeError):
        return "0000-01-01"
    return (d - datetime.timedelta(days=30)).isoformat()


class RuleIn(BaseModel):
    scope: str = "global"
    symbol: Optional[str] = None
    kind: str  # volume_ratio | price_move | announcement_category
    threshold: Optional[float] = None
    categories: Optional[str] = None
    enabled: bool = True


@router.get("/alerts/rules")
def list_rules():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM alert_rules ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/alerts/rules", status_code=201)
def create_rule(rule: RuleIn):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "INSERT INTO alert_rules(scope,symbol,kind,threshold,categories,enabled) VALUES(?,?,?,?,?,?)",
        (rule.scope, rule.symbol, rule.kind, rule.threshold, rule.categories, int(rule.enabled))
    )
    conn.commit()
    rule_id = cur.lastrowid
    conn.close()
    return {"id": rule_id, **rule.model_dump()}


@router.put("/alerts/rules/{rule_id}")
def update_rule(rule_id: int, rule: RuleIn):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE alert_rules SET scope=?,symbol=?,kind=?,threshold=?,categories=?,enabled=? WHERE id=?",
        (rule.scope, rule.symbol, rule.kind, rule.threshold, rule.categories, int(rule.enabled), rule_id)
    )
    conn.commit()
    conn.close()
    return {"id": rule_id, **rule.model_dump()}


@router.get("/alerts/events")
def list_events(symbol: str = "", limit: int = 100):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    q = "SELECT * FROM alert_events WHERE date>=?"
    params = [_recent_cutoff(conn)]
    if symbol:
        q += " AND symbol=?"
        params.append(symbol.upper())
    q += " ORDER BY date DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    out = [_enrich(r, conn) for r in rows]
    conn.close()
    return out


@router.post("/alerts/scan")
def trigger_scan():
    from backend.push.alerts_scan import scan_alerts
    fired = scan_alerts()
    return {"fired": fired, "ts": datetime.datetime.utcnow().isoformat()}


@router.get("/alerts/dryrun")
def dry_run():
    """Preview what would fire in the last 30 days (read-only, no insert)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    events = conn.execute(
        """SELECT e.*, r.kind, r.threshold, r.categories
           FROM alert_events e JOIN alert_rules r ON e.rule_id=r.id
           WHERE e.date>=?
           ORDER BY e.date DESC, e.id DESC LIMIT 200""",
        [_recent_cutoff(conn)],
    ).fetchall()
    enriched = [_enrich(e, conn) for e in events]
    conn.close()
    return {
        "total_would_fire": len(enriched),
        "events": enriched,
    }
