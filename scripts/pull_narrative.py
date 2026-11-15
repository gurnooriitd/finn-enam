"""One strong-model pass per company: business lines, bull, bear.

Every stored sentence cites a chunk id that was actually in the prompt,
or "tables" for a figure we computed from screener. Uncited lines are dropped.

    python scripts/pull_narrative.py
"""
import datetime
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import BaseModel, Field

from backend.config import DB_PATH
from backend.llm.gateway import call_llm
from backend.pull.narrative import ensure_table
from backend.pull.retrieve import retrieve

SYSTEM = """You write a company research note from the excerpts and the TABLES block only.
You have no other knowledge of this company. If a fact is not in the excerpts or TABLES, leave it out.

Rules:
- business: up to 5 short sentences on what the company does and, if stated, how revenue splits.
- bull: up to 3 sentences. bear: up to 3 sentences. Each is one concrete fact, not a slogan.
- chunk_id must be copied from an excerpt id, or the literal word tables when the sentence uses only a TABLES figure.
- Do not mention a number that is not in TABLES or in the excerpt you cite.
- Return {"business": [], "bull": [], "bear": []} for any side you cannot support.
"""


class Line(BaseModel):
    text: str
    chunk_id: str


class Note(BaseModel):
    business: list[Line] = Field(default_factory=list)
    bull: list[Line] = Field(default_factory=list)
    bear: list[Line] = Field(default_factory=list)


def _full(conn, symbol, chunk_id) -> str:
    row = conn.execute(
        "SELECT text, page, doc_kind, period, section_hint FROM doc_chunks WHERE symbol=? AND chunk_id=?",
        (symbol, chunk_id),
    ).fetchone()
    if not row:
        return ""
    text = re.sub(r"\s+", " ", row["text"] or "").strip()
    return f"[{chunk_id} p.{row['page']} {row['doc_kind']} {row['period']} {row['section_hint']}] {text[:900]}"


def _context(conn, symbol) -> tuple[str, set[str]]:
    queries = [
        "business overview what the company does segments revenue",
        "growth opportunity order book margin expansion",
        "risk pressure competition debt contingent liabilities borrowings",
    ]
    ids: list[str] = []
    seen = set()
    for q in queries:
        for hit in retrieve(symbol, q, k=4):
            if hit["chunk_id"] not in seen:
                seen.add(hit["chunk_id"])
                ids.append(hit["chunk_id"])
    blocks = [b for cid in ids if (b := _full(conn, symbol, cid))]
    tables = _tables(conn, symbol)
    body = "TABLES (cite as chunk_id tables):\n" + tables + "\n\nEXCERPTS:\n" + "\n\n".join(blocks)
    return body, set(ids) | {"tables"}


def _tables(conn, symbol) -> str:
    lines = []
    for table, metric, label in (
        ("profit_loss", "Sales+", "sales_cr"),
        ("profit_loss", "Revenue+", "revenue_cr"),
        ("profit_loss", "OPM %", "operating_margin_pct"),
        ("profit_loss", "Net Profit+", "net_profit_cr"),
        ("ratios", "ROCE %", "roce_pct"),
        ("ratios", "ROE %", "roe_pct"),
    ):
        rows = conn.execute(
            "SELECT period, value FROM screener_fundamentals "
            "WHERE symbol=? AND table_type=? AND metric=? AND period!='TTM' AND value IS NOT NULL",
            (symbol, table, metric),
        ).fetchall()
        if not rows:
            continue
        rows.sort(key=lambda r: r["period"])
        tail = ", ".join(f"{r['period']} {r['value']:g}" for r in rows[-4:])
        lines.append(f"{label}: {tail}")
    return "\n".join(lines) or "no standardised table rows"


def _keep(lines: list[Line], allowed: set[str]) -> list[dict]:
    out = []
    for line in lines[:6]:
        cid = (line.chunk_id or "").strip()
        text = re.sub(r"\s+", " ", line.text or "").strip()
        if cid not in allowed or len(text.split()) < 5:
            continue
        out.append({"text": text, "chunk_id": cid})
    return out[:5]


def run_symbol(symbol: str, force: bool = False):
    symbol = symbol.upper()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    if not force:
        have = conn.execute("SELECT 1 FROM pull_narrative WHERE symbol=?", (symbol,)).fetchone()
        if have:
            print(f"{symbol}: cached")
            conn.close()
            return
    n_chunks = conn.execute(
        "SELECT COUNT(*) FROM doc_chunks WHERE symbol=?", (symbol,)
    ).fetchone()[0]
    if not n_chunks:
        print(f"{symbol}: no corpus")
        conn.close()
        return
    body, allowed = _context(conn, symbol)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Company ticker: {symbol}.\n\n{body}"},
    ]
    try:
        note = call_llm(messages, tier="strong", step="narrative", response_schema=Note)
    except Exception as e:
        print(f"{symbol}: LLM error {e}")
        conn.close()
        return
    payload = {
        "business": _keep(note.business, allowed),
        "bull": _keep(note.bull, allowed)[:3],
        "bear": _keep(note.bear, allowed)[:3],
        "note": "Each line cites an annual-report or concall excerpt, or the screener tables.",
    }
    conn.execute(
        "INSERT OR REPLACE INTO pull_narrative(symbol, payload, created_at) VALUES (?,?,?)",
        (symbol, json.dumps(payload), datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    print(f"{symbol}: business {len(payload['business'])} bull {len(payload['bull'])} bear {len(payload['bear'])}")


def main():
    conn = sqlite3.connect(DB_PATH)
    symbols = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM doc_chunks ORDER BY symbol"
    )]
    conn.close()
    for s in symbols:
        run_symbol(s)


if __name__ == "__main__":
    main()
