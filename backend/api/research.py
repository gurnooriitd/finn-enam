"""backend/api/research.py — deterministic company dossier from screener tables.

No LLM, no network: everything is computed from `screener_fundamentals`
(screener.in's standardised long-format tables, unit = Rs crore).
Missing data is returned as null and rendered "not available" — never guessed.
"""
import json
import re
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.config import DB_PATH, FINN_MODE, LLM_API_KEY, PORTFOLIO_PATH, RAW_DIR
from backend.pull.narrative import narrative_for
from backend.pull.promised import corpus_symbols, promised_for
from backend.pull.retrieve import retrieve

router = APIRouter()

# Screener's standardised layout differs for banks/NBFCs: Revenue+/Financing
# Margin %/Borrowing instead of Sales+/OPM %/Borrowings+.
REVENUE = ("Sales+", "Revenue+")
OPMARGIN = ("OPM %", "Financing Margin %")
BORROW = ("Borrowings+", "Borrowing")
BANK_MARK = "Financing Profit"  # only banks/NBFCs have this row


def _connect():
    return sqlite3.connect(DB_PATH)


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _fy(period: str):
    m = re.search(r"(\d{4})", period or "")
    return int(m.group(1)) if m else None


def _series(conn, symbol, table_type, metric):
    """Full FY series for one metric, oldest first, excluding TTM rows."""
    rows = conn.execute(
        "SELECT period, value FROM screener_fundamentals "
        "WHERE symbol=? AND table_type=? AND metric=? AND period!='TTM'",
        (symbol, table_type, metric),
    ).fetchall()
    out = [(p, _num(v)) for p, v in rows if _fy(p)]
    out.sort(key=lambda x: _fy(x[0]))
    return out


def _latestfy(series):
    for p, v in reversed(series):
        if v is not None:
            return p, v
    return (None, None)


def _ttm(conn, symbol, metric):
    row = conn.execute(
        "SELECT value FROM screener_fundamentals "
        "WHERE symbol=? AND table_type='profit_loss' AND metric=? AND period='TTM'",
        (symbol, metric),
    ).fetchone()
    return _num(row[0]) if row else None


def _cagr(series, metric_fallback=None):
    """CAGR over the shown window (first -> last non-null), None if not computable."""
    pts = [(p, v) for p, v in series if v is not None]
    if len(pts) < 4 or pts[0][1] <= 0:
        return None
    first, last = pts[0][1], pts[-1][1]
    years = _fy(pts[-1][0]) - _fy(pts[0][0])
    if years <= 0 or first <= 0:
        return None
    return round(((last / first) ** (1 / years) - 1) * 100, 1)


def _company_name(symbol):
    for variant in ("consolidated", "standalone"):
        p = RAW_DIR / "screener" / f"{symbol}_{variant}.html"
        if p.exists():
            m = re.search(r"<title>([^<\n|-]+)", p.read_text(errors="ignore"))
            if m:
                return re.sub(r"\s+share price\s*$", "", m.group(1).strip())
    return symbol


def _tiles(conn, symbol, is_bank):
    """Financial snapshot: latest value + FY sparkline for each headline metric."""
    def tile(label, series, *, unit, pct=False, ttm=None, note=None):
        p, v = _latestfy(series)
        if ttm is not None:
            v, p = ttm, "TTM"
        return {
            "label": label, "value": v, "period": p, "unit": unit, "pct": pct,
            "spark": [x[1] for x in series if x[1] is not None],
            "spark_labels": [x[0] for x in series if x[1] is not None],
            **({"note": note} if note else {}),
        }

    rev = _series(conn, symbol, "profit_loss", REVENUE[0]) or _series(conn, symbol, "profit_loss", REVENUE[1])
    opm = _series(conn, symbol, "profit_loss", OPMARGIN[0]) or _series(conn, symbol, "profit_loss", OPMARGIN[1])
    pat = _series(conn, symbol, "profit_loss", "Net Profit+")
    eps = _series(conn, symbol, "profit_loss", "EPS in Rs")
    roce = _series(conn, symbol, "ratios", "ROCE %")
    roe = _series(conn, symbol, "ratios", "ROE %")
    tiles = [
        tile("Revenue", rev, unit="cr", ttm=_ttm(conn, symbol, REVENUE[0]) or _ttm(conn, symbol, REVENUE[1])),
        tile("Financing margin" if is_bank else "Operating margin", opm, unit="%", pct=True,
             ttm=_ttm(conn, symbol, OPMARGIN[0]) or _ttm(conn, symbol, OPMARGIN[1]),
             note=("screener's financing margin; banks report NIM differently"
                   if is_bank else "EBITDA margin")),
        tile("Net profit", pat, unit="cr", ttm=_ttm(conn, symbol, "Net Profit+")),
        tile("EPS", eps, unit="Rs", ttm=_ttm(conn, symbol, "EPS in Rs")),
        tile("ROCE", roce, unit="%", pct=True,
             note="not disclosed for banks" if is_bank else None),
        tile("ROE", roe, unit="%", pct=True),
    ]
    return tiles


def _trajectory(conn, symbol):
    rev = _series(conn, symbol, "profit_loss", REVENUE[0]) or _series(conn, symbol, "profit_loss", REVENUE[1])
    pat = _series(conn, symbol, "profit_loss", "Net Profit+")
    opm = _series(conn, symbol, "profit_loss", OPMARGIN[0]) or _series(conn, symbol, "profit_loss", OPMARGIN[1])
    n = 6  # last ~6 FYs
    return {
        "sales": [{"p": p, "v": v} for p, v in rev[-n:]],
        "profit": [{"p": p, "v": v} for p, v in pat[-n:]],
        "margin": [{"p": p, "v": v} for p, v in opm[-n:]],
        "sales_cagr": _cagr(rev),
        "profit_cagr": _cagr(pat),
    }


def _balance(conn, symbol, is_bank):
    bor = _series(conn, symbol, "balance_sheet", BORROW[0]) or _series(conn, symbol, "balance_sheet", BORROW[1])
    eq = _series(conn, symbol, "balance_sheet", "Equity Capital")
    res = _series(conn, symbol, "balance_sheet", "Reserves")
    # D/E on the latest period where both sides exist
    de = None
    if bor and (eq or res):
        bor_map = dict(bor)
        base = dict(eq) if eq else {}
        for p, r in (res or []):
            base[p] = (base.get(p) or 0) + (r or 0)
        for p in sorted(base, key=_fy, reverse=True):
            networth, b = base.get(p), bor_map.get(p)
            if networth and b is not None:
                de = round(b / networth, 2)
                break
    days = {}
    for key, metric in (("debtor", "Debtor Days"), ("inventory", "Inventory Days"), ("payable", "Days Payable")):
        s = _series(conn, symbol, "ratios", metric)
        p, v = _latestfy(s)
        days[key] = {"value": v, "period": p}
    note = None
    if is_bank:
        note = "Banks fund themselves mainly with deposits; debt/equity and working-capital days are not meaningful."
    n = 8
    return {
        "borrowings": [{"p": p, "v": v} for p, v in bor[-n:]],
        "de": de,
        "days": days,
        **({"note": note} if note else {}),
    }


def _cash(conn, symbol, is_bank):
    cfo = dict(_series(conn, symbol, "cash_flow", "Cash from Operating Activity+"))
    pat = {p: v for p, v in _series(conn, symbol, "profit_loss", "Net Profit+")}
    years = sorted(set(cfo) | set(pat), key=_fy)[-8:]
    years = [y for y in years if _fy(y)]
    rows = [
        {"p": p, "cfo": cfo.get(p), "profit": pat.get(p),
         "ratio": round(cfo[p] / pat[p], 2) if cfo.get(p) is not None and pat.get(p) else None}
        for p in years
    ]
    cum_cfo = sum(r["cfo"] for r in rows if r["cfo"] is not None)
    cum_pat = sum(r["profit"] for r in rows if r["profit"] is not None)
    cum_ratio = round(cum_cfo / cum_pat, 2) if cum_pat and cum_cfo is not None else None
    # For banks the ratio is structurally misleading (loan growth sits in
    # investing activities), so compute it but never raise the flag.
    flag = bool(
        not is_bank and len(rows) >= 3 and cum_pat and cum_cfo is not None
        and cum_cfo < 0.7 * cum_pat
    )
    note = None
    if is_bank:
        note = "For banks, operating cash flow excludes loan growth (shown as investing); the ratio is not comparable to other companies."
    return {
        "years": rows,
        "cum_cfo": round(cum_cfo, 1) if cum_cfo else None,
        "cum_profit": round(cum_pat, 1) if cum_pat else None,
        "cum_ratio": cum_ratio,
        "flag": flag,
        **({"note": note} if note else {}),
    }


@router.get("")
def research_symbols():
    """Tickers that have screener data, portfolio first, then the rest alphabetically."""
    try:
        pf = [h["symbol"] for h in json.loads(PORTFOLIO_PATH.read_text())["holdings"]]
    except Exception:
        pf = []
    conn = _connect()
    have = sorted(r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM screener_fundamentals ORDER BY symbol"))
    try:
        calls = {r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM doc_chunks WHERE doc_kind='CONCALL'")}
        # Open Research on the ticker with the most extracted promises, so the
        # first screen is a checkable call rather than an empty transcript.
        weight = {r[0]: r[1] for r in conn.execute(
            "SELECT symbol, COUNT(*) FROM guidance_items GROUP BY symbol")}
    except Exception:
        calls, weight = set(), {}
    conn.close()
    ordered = sorted(calls, key=lambda s: (-weight.get(s, 0), s not in pf, s))
    return {
        "portfolio": [s for s in pf if s in have],
        "more": [s for s in have if s not in pf],
        "with_calls": ordered,
    }


@router.get("/{symbol}/promised")
def promised(symbol: str):
    symbol = symbol.upper()
    if not re.fullmatch(r"[A-Z0-9.&-]{1,20}", symbol):
        raise HTTPException(400, "bad symbol")
    payload = promised_for(symbol)
    payload["corpus"] = corpus_symbols()
    return payload


@router.get("/{symbol}/narrative")
def narrative(symbol: str):
    symbol = symbol.upper()
    if not re.fullmatch(r"[A-Z0-9.&-]{1,20}", symbol):
        raise HTTPException(400, "bad symbol")
    return narrative_for(symbol)


class _AskLine(BaseModel):
    text: str
    chunk_id: str


class _AskAnswer(BaseModel):
    sentences: list[_AskLine] = Field(default_factory=list)


_ASK_SYSTEM = (
    "Answer the question using only the excerpts. You have no other knowledge of this company. "
    "Every sentence's chunk_id must be copied from an excerpt id. "
    "Do not state a number that is not in the excerpt you cite. "
    "If the excerpts do not contain the answer, return {\"sentences\": []}. "
    "At most 4 short sentences."
)


def _chunk_text(symbol: str, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    conn = sqlite3.connect(DB_PATH)
    try:
        q = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT chunk_id, text FROM doc_chunks WHERE symbol=? AND chunk_id IN ({q})",
            (symbol, *ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {r[0]: re.sub(r"\s+", " ", r[1] or "").strip() for r in rows}


def _answer_from_hits(symbol: str, question: str, hits: list[dict]) -> tuple[list[dict], str]:
    allowed = {h["chunk_id"] for h in hits}
    if FINN_MODE == "snapshot" or not LLM_API_KEY:
        return [], "Pages from the filings. A written answer needs the model, which is off in this copy."
    fuller = _chunk_text(symbol, [h["chunk_id"] for h in hits])
    blocks = []
    for h in hits:
        text = fuller.get(h["chunk_id"]) or h["text"]
        blocks.append(f"[{h['chunk_id']} p.{h.get('page')} {h.get('doc_kind')} {h.get('period')}]\n{text[:1600]}")
    try:
        from backend.llm.gateway import call_llm
        parsed = call_llm(
            [
                {"role": "system", "content": _ASK_SYSTEM},
                {"role": "user", "content": f"{symbol}: {question}\n\n" + "\n\n".join(blocks)},
            ],
            tier="strong",
            step="ask",
            response_schema=_AskAnswer,
        )
    except Exception:
        return [], "The pages are below. A written answer could not be produced from them."
    kept = []
    for line in parsed.sentences:
        raw = (line.chunk_id or "").strip().strip("[]")
        cid = raw if raw in allowed else next((a for a in allowed if a in raw), "")
        text = re.sub(r"\s+", " ", line.text or "").strip()
        if cid and len(text.split()) >= 4:
            kept.append({"text": text, "chunk_id": cid})
    if not kept:
        return [], "Not disclosed in the retrieved pages."
    return kept[:4], "Answered only from the pages below. A sentence without a real page id was dropped."


@router.get("/{symbol}/ask")
def ask(symbol: str, q: str = ""):
    """Answer from this company's filings only. Uncited sentences are dropped."""
    symbol = symbol.upper()
    if not re.fullmatch(r"[A-Z0-9.&-]{1,20}", symbol):
        raise HTTPException(400, "bad symbol")
    question = (q or "").strip()
    if len(question) < 3:
        raise HTTPException(400, "ask a longer question")
    hits = retrieve(symbol, question, k=5)
    if not hits:
        return {
            "symbol": symbol, "question": question, "hits": [], "answer": [],
            "note": "No annual-report or concall excerpts are loaded for this ticker.",
        }
    answer, note = _answer_from_hits(symbol, question, hits)
    return {
        "symbol": symbol, "question": question, "hits": hits,
        "answer": answer, "note": note,
    }


@router.get("/{symbol}")
def dossier(symbol: str):
    conn = _connect()
    try:
        has = conn.execute(
            "SELECT 1 FROM screener_fundamentals WHERE symbol=? LIMIT 1", (symbol,)
        ).fetchone()
        if not has:
            return {"symbol": symbol, "available": False,
                    "note": "no screener data ingested for this ticker"}
        is_bank = bool(conn.execute(
            "SELECT 1 FROM screener_fundamentals WHERE symbol=? AND metric=? LIMIT 1",
            (symbol, BANK_MARK)).fetchone())
        as_of = conn.execute(
            "SELECT MAX(fetched_at) FROM screener_fundamentals WHERE symbol=?", (symbol,)
        ).fetchone()[0]
        return {
            "symbol": symbol,
            "name": _company_name(symbol),
            "available": True,
            "is_bank": is_bank,
            "as_of": (as_of or "")[:10],
            "unit": "Rs crore",
            "snapshot": _tiles(conn, symbol, is_bank),
            "trajectory": _trajectory(conn, symbol),
            "balance": _balance(conn, symbol, is_bank),
            "cash": _cash(conn, symbol, is_bank),
        }
    finally:
        conn.close()
