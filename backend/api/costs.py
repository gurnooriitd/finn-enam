"""
API — Costs
GET /api/costs — LLM ledger summary
"""
import sqlite3
from fastapi import APIRouter
from backend.config import DB_PATH

router = APIRouter()


@router.get("/costs")
def get_costs():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """SELECT model, step,
                  COUNT(*) as calls,
                  SUM(tokens_in) as tokens_in,
                  SUM(tokens_out) as tokens_out,
                  SUM(est_cost) as est_cost,
                  SUM(cached) as cached_hits
           FROM llm_ledger
           GROUP BY model, step
           ORDER BY est_cost DESC"""
    ).fetchall()

    totals = conn.execute(
        """SELECT SUM(est_cost) as total_cost,
                  SUM(tokens_in+tokens_out) as total_tokens,
                  SUM(cached) as cache_hits,
                  COUNT(*) as total_calls
           FROM llm_ledger"""
    ).fetchone()

    conn.close()
    return {
        "totals": dict(totals) if totals else {},
        "by_model_step": [dict(r) for r in rows],
    }
