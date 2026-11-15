"""Read explicit forward guidance out of the last four concalls of a ticker.

One cheap-model call per transcript. Numbers are NOT taken from the model:
they are parsed from the verbatim quote, and the quote must actually occur
in the cited chunk. Results are cached in guidance_items. Re-runs skip a
call that already has a guidance_runs row unless --force.

    python scripts/extract_guidance.py TITAN DIXON
    python scripts/extract_guidance.py --all
"""
import argparse
import datetime
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import BaseModel, Field

from backend.config import DB_PATH
from backend.llm.gateway import call_llm
from backend.pull.promised import (
    ensure_table, parse_qfy, quote_in_chunk, resolve_horizon, targets_from_quote,
)

CUE = re.compile(
    r"guidan|outlook|we expect|expect(?:ed|s)? to|we see|target|aim to|should be|"
    r"will be|margin|growth|capex|basis point|double[- ]digit|single[- ]digit|"
    r"teens|percent|%",
    re.I,
)

KEEP_METRICS = {"revenue", "operating_margin", "pat", "volume", "capex", "other"}


class Item(BaseModel):
    metric: str
    comparison: str
    horizon: str
    qualifier: str = "firm"
    quote: str
    speaker: str | None = None
    chunk_id: str


class Batch(BaseModel):
    items: list[Item] = Field(default_factory=list)


SYSTEM = """You extract explicit forward-looking guidance from one Indian earnings call.
The user message contains excerpts, each starting with an id like [CONCALL-Q2FY27-p4 p.4].

Rules:
- Only management speaking about a FUTURE period: next quarter, the rest of the year, a named FY, or medium term.
- Skip the recap of the quarter just reported. Skip analyst questions.
- metric is one of: revenue, operating_margin, pat, volume, capex, other.
- comparison is one of: growth (a percent change), level (a margin percent or an absolute figure), direction (up, down, or flat, with no number).
- quote is at most 25 words, copied verbatim from one excerpt. Do not paraphrase, do not join two sentences from different speakers.
- chunk_id is the id in brackets, without the page suffix. Example: CONCALL-Q2FY27-p4.
- horizon is a short label such as "next quarter", "FY27", "Q3FY27", "full year", "medium term".
- qualifier is "firm" or "hedged".
- Return at most 5 items. Prefer statements that contain a number. If nothing qualifies, return {"items": []}.
"""


def _chunks(conn, symbol, period, k=5):
    rows = conn.execute(
        "SELECT chunk_id, page, text FROM doc_chunks "
        "WHERE symbol=? AND doc_kind='CONCALL' AND period=?",
        (symbol, period),
    ).fetchall()
    scored = []
    for r in rows:
        n = len(CUE.findall(r["text"] or ""))
        if n:
            scored.append((n, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _n, r in scored[:k]]


def _last_calls(conn, symbol, n=4):
    periods = [r[0] for r in conn.execute(
        "SELECT DISTINCT period FROM doc_chunks WHERE symbol=? AND doc_kind='CONCALL'",
        (symbol,),
    ).fetchall()]
    periods = [p for p in periods if parse_qfy(p or "")]
    periods.sort(key=lambda p: (parse_qfy(p)[1], parse_qfy(p)[0]))
    return periods[-n:]


def _prompt(symbol, period, chunks):
    blocks = []
    for r in chunks:
        text = re.sub(r"\s+", " ", r["text"] or "")[:1000]
        blocks.append(f"[{r['chunk_id']} p.{r['page']}] {text}")
    user = (
        f"Company: {symbol}. Call period: {period} (this call discusses {period} results).\n\n"
        + "\n\n".join(blocks)
    )
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]


def _clean(symbol, period, chunks, batch: Batch):
    by_id = {r["chunk_id"]: r for r in chunks}
    kept = []
    dropped = []
    for it in batch.items[:6]:
        metric = it.metric if it.metric in KEEP_METRICS else "other"
        chunk = by_id.get(it.chunk_id)
        if chunk is None:
            dropped.append("bad chunk id")
            continue
        quote = re.sub(r"\s+", " ", it.quote or "").strip().strip('"')
        words = quote.split()
        if len(words) < 6:
            dropped.append("quote too short")
            continue
        if len(words) > 32:
            quote = " ".join(words[:32])
        if not quote_in_chunk(quote, chunk["text"] or ""):
            dropped.append("quote not in chunk")
            continue
        parsed = targets_from_quote(quote)
        if not parsed:
            dropped.append("no target in quote")
            continue
        if parsed["unit"] == "direction" and metric not in ("revenue", "operating_margin", "pat"):
            dropped.append("direction on an uncheckable metric")
            continue
        kind, _spec = resolve_horizon(it.horizon, period)
        if kind == "past":
            dropped.append("already-reported period")
            continue
        comparison = it.comparison if it.comparison in ("growth", "level", "direction") else "growth"
        if parsed["unit"] == "direction":
            comparison = "direction"
        elif parsed["unit"] == "pp":
            comparison = "delta_pp"
        kept.append({
            "symbol": symbol,
            "call_period": period,
            "chunk_id": it.chunk_id,
            "page": chunk["page"],
            "metric": metric,
            "comparison": comparison,
            "horizon": (it.horizon or "unspecified")[:80],
            "qualifier": "hedged" if it.qualifier == "hedged" else "firm",
            "quote": quote,
            "speaker": (it.speaker or "")[:80] or None,
            "low": parsed["low"],
            "high": parsed["high"],
            "interpreted": int(bool(parsed["interpreted"])),
        })
    return kept, dropped


def run_symbol(symbol: str, force: bool = False):
    symbol = symbol.upper()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    calls = _last_calls(conn, symbol)
    print(f"{symbol}: last calls {calls}")
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    for period in calls:
        if not force:
            done = conn.execute(
                "SELECT status, n_kept FROM guidance_runs WHERE symbol=? AND call_period=?",
                (symbol, period),
            ).fetchone()
            if done:
                print(f"  {period}: skip ({done['status']}, kept {done['n_kept']})")
                continue
        chunks = _chunks(conn, symbol, period)
        if not chunks:
            conn.execute(
                "INSERT OR REPLACE INTO guidance_runs(symbol, call_period, n_kept, status, created_at) VALUES (?,?,?,?,?)",
                (symbol, period, 0, "no_cues", now),
            )
            conn.commit()
            print(f"  {period}: no guidance cues")
            continue
        try:
            batch = call_llm(_prompt(symbol, period, chunks), tier="cheap", step="guidance_extract", response_schema=Batch)
        except Exception as e:
            conn.execute(
                "INSERT OR REPLACE INTO guidance_runs(symbol, call_period, n_kept, status, created_at) VALUES (?,?,?,?,?)",
                (symbol, period, 0, "error", now),
            )
            conn.commit()
            print(f"  {period}: LLM error {e}")
            continue
        kept, dropped = _clean(symbol, period, chunks, batch)
        if force:
            conn.execute("DELETE FROM guidance_items WHERE symbol=? AND call_period=?", (symbol, period))
        for item in kept:
            conn.execute(
                """INSERT OR REPLACE INTO guidance_items
                   (symbol, call_period, chunk_id, page, metric, comparison, horizon, qualifier,
                    quote, speaker, low, high, interpreted, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item["symbol"], item["call_period"], item["chunk_id"], item["page"], item["metric"],
                 item["comparison"], item["horizon"], item["qualifier"], item["quote"], item["speaker"],
                 item["low"], item["high"], item["interpreted"], now),
            )
        conn.execute(
            "INSERT OR REPLACE INTO guidance_runs(symbol, call_period, n_kept, status, created_at) VALUES (?,?,?,?,?)",
            (symbol, period, len(kept), "ok", now),
        )
        conn.commit()
        print(f"  {period}: kept {len(kept)} dropped {len(dropped)} {dropped}")
        for item in kept:
            print(f"    {item['metric']}/{item['comparison']} [{item['horizon']}] {item['quote'][:110]}")
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    symbols = [s.upper() for s in args.symbols]
    if args.all or not symbols:
        conn = sqlite3.connect(DB_PATH)
        symbols = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM doc_chunks WHERE doc_kind='CONCALL' ORDER BY symbol"
        )]
        conn.close()
    for s in symbols:
        run_symbol(s, force=args.force)


if __name__ == "__main__":
    main()
