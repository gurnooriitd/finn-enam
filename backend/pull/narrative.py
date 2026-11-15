"""Cited business snapshot and bull/bear, served from a cached table.

The script that fills the table is scripts/pull_narrative.py. This module
only reads. A line is stored only if its citation was in the prompt.
"""
import json
import re
import sqlite3

from backend.config import DB_PATH


def ensure_table(conn: sqlite3.Connection | None = None):
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pull_narrative (
            symbol TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT
        )
    """)
    if own:
        conn.commit()
        conn.close()


def narrative_for(symbol: str) -> dict:
    symbol = (symbol or "").upper()
    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        row = conn.execute(
            "SELECT payload FROM pull_narrative WHERE symbol=?", (symbol,)
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if not row:
        return {
            "symbol": symbol,
            "status": "not_extracted",
            "business": [],
            "bull": [],
            "bear": [],
            "note": "No cited narrative is stored for this ticker.",
        }
    data = json.loads(row[0])
    data["symbol"] = symbol
    if not data.get("business"):
        excerpt = _overview_excerpt(symbol)
        if excerpt:
            data["business"] = [excerpt]
            data["note"] = (data.get("note") or "") + " The business line is an excerpt, not a summary."
    data["status"] = "ready" if (data.get("business") or data.get("bull") or data.get("bear")) else "empty"
    return data


def _overview_excerpt(symbol: str) -> dict | None:
    """When the model kept no business sentence, show one annual-report page as-is."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT chunk_id, text FROM doc_chunks WHERE symbol=? AND doc_kind='AR' "
            "AND section_hint IN ('business_overview', 'mda', 'segment') AND length(text) > 200 "
            "ORDER BY period DESC, page LIMIT 12",
            (symbol,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    best = None
    best_score = -999
    for row in rows:
        text = re.sub(r"\s+", " ", row[1] or "").strip()
        if len(text) < 80:
            continue
        low = text.lower()
        score = 0
        if re.search(r"about (the company|infosys|us)\b|we are a|engaged in the business", low):
            score += 6
        if "employee" in low and "revenue" in low:
            score += 3
        if low.count("“") + low.count('"') >= 2:
            score -= 4
        if re.search(r"notice of|chairman.s statement|board of directors", low):
            score -= 4
        if score > best_score:
            best, best_score = (row[0], text), score
    if not best:
        return None
    return {"text": best[1][:360].rstrip() + "…", "chunk_id": best[0]}
