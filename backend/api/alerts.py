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

router = APIRouter()


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
    q = "SELECT * FROM alert_events"
    params = []
    if symbol:
        q += " WHERE symbol=?"
        params.append(symbol.upper())
    q += " ORDER BY fired_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
           ORDER BY e.fired_at DESC LIMIT 200"""
    ).fetchall()
    conn.close()
    return {
        "total_would_fire": len(events),
        "events": [dict(e) for e in events],
    }
