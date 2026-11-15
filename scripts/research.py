"""
research.py SYMBOL [--refresh] — build the pull corpus (docs + chunks + BM25) for ANY NSE ticker.

Steps: resolve screener page (existing ingest) -> download latest 2 ARs + last 6 concall
transcripts (raw cache data/raw/docs/) -> PyMuPDF page text -> page/section chunks ->
BM25 retrieve(). No LLM, no hard-coded symbols. Snapshot mode: DB + cached PDFs only.
"""
import argparse
import datetime
import hashlib
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests
import pymupdf

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.config import DB_PATH, RAW_DIR, NSE_SLEEP, FINN_MODE  # noqa: E402
from backend.ingest import screener  # noqa: E402

DOCS_RAW = RAW_DIR / "docs"
DOCS_RAW.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/pdf,*/*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}

# AR sections the pull pipeline consumes (AGENTS 7.2). Heading detection + page
# keyword fallback; everything else in a 300-page AR is skipped to keep the DB small.
AR_SECTIONS = [
    ("mda", ["management discussion", "management's discussion", "mda"]),
    ("business_overview", ["business overview", "company overview", "our business", "corporate overview", "about us"]),
    ("segment", ["segment information", "segment reporting", "segment note", "business segment"]),
    ("borrowings", ["borrowings"]),
    ("contingent_liabilities", ["contingent liabilities"]),
    ("related_party", ["related party", "related-party", "related parties"]),
    ("auditor", ["key audit matters", "auditor's report", "independent auditor", "audit report"]),
    ("cash_flow", ["cash flow statement", "statement of cash flows", "cash flow"]),
]
MAX_CHUNKS_PER_SECTION = 30
MAX_CHUNKS_PER_AR = 100
CHUNK_TARGET = 2400   # ~600 tokens
CHUNK_MIN = 900
CHUNK_OVERLAP = 220

STOPWORDS = set("the a an of and or to in for on with by is are was were be as at from that this it its we our you your they their he she his her not no yes if but so than then will would shall should can could may might have has had do does did also more most other such only each any all per vs p c y o q s t".split())


# ---------- schema ----------

def ensure_tables(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS doc_chunks (
        symbol TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        doc_id INTEGER,
        page INTEGER,
        doc_kind TEXT,
        period TEXT,
        section_hint TEXT,
        text TEXT,
        PRIMARY KEY (symbol, chunk_id)
    );
    CREATE TABLE IF NOT EXISTS doc_ingest_log (
        doc_id INTEGER PRIMARY KEY,
        symbol TEXT, status TEXT, reason TEXT,
        n_chunks INTEGER, fetched_at TEXT
    );
    """)
    conn.commit()


# ---------- doc selection ----------

def _transcript(r: sqlite3.Row) -> bool:
    t = (r["period"] or "").lower()
    return "transcript" in t or "transcript" in r["url"].lower()


def _is_ar(r: sqlite3.Row) -> bool:
    # true screener doc label; junk rows (CP repayments etc.) lack it
    return "annual report" in (r["period"] or "").lower()


def _ar_year(r: sqlite3.Row) -> int:
    t = r["period"] or ""
    m = re.search(r"fy\s*(\d{2,4})", t, re.I)
    if m:
        y = int(m.group(1))
        return y + 2000 if y < 100 else y
    years = [int(x) for x in re.findall(r"(20\d{2})", t)]
    return max(years) if years else 0


def select_docs(conn: sqlite3.Connection, symbol: str):
    rows = conn.execute(
        "SELECT * FROM docs WHERE symbol=? AND url LIKE '%.pdf%' ORDER BY id", (symbol,)
    ).fetchall()
    ars = sorted([r for r in rows if _is_ar(r)], key=_ar_year, reverse=True)[:2]
    calls = [r for r in rows if _transcript(r)][:6]  # screener lists newest first
    return ars, calls


# ---------- download ----------

def download_pdf(url: str) -> tuple[Path | None, str]:
    """Return (path, status). status: cached | downloaded | error:<reason>."""
    if "pdf-viewer?pdf=" in url:  # site viewer page -> direct asset
        import urllib.parse
        url = urllib.parse.urljoin(url, urllib.parse.unquote(url.split("pdf=", 1)[1]))
    path = DOCS_RAW / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
    if path.exists() and path.stat().st_size > 200:
        return path, "cached"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        time.sleep(NSE_SLEEP)
        if r.status_code != 200:
            return None, f"error:http {r.status_code}"
        if not r.content.startswith(b"%PDF"):
            return None, "error:not a pdf (blocked or html)"
        path.write_bytes(r.content)
        return path, "downloaded"
    except Exception as e:
        return None, f"error:{type(e).__name__}: {str(e)[:80]}"


# ---------- period labels ----------

def _quarter_label(text: str) -> str | None:
    m = re.search(r"q([1-4])\s*fy\s*(\d{2,4})", text, re.I)
    if not m:
        return None
    y = int(m.group(2))
    return f"Q{m.group(1)}FY{str(y % 100).zfill(2)}"


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"])}


def _quarter_from_call_date(text: str) -> str | None:
    """Transcript footers carry the call date ('November 04, 2025'). The call
    follows the quarter it discusses by ~1 month: end_month = month - 3."""
    hits = re.findall(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s+(20\d{2})",
        text, re.I)
    if not hits:
        return None
    mon, year = hits[-1][0].lower(), int(hits[-1][1])
    em = _MONTHS[mon] - 3
    ey = year if em >= 1 else year - 1
    if em <= 0:
        em += 12
    q = {4: 1, 7: 2, 10: 3, 1: 4}.get(em, 1 + (em - 4) // 3)
    fy = ey + 1 if em >= 4 else ey
    return f"Q{q}FY{fy % 100:02d}"


def period_label(kind: str, link_text: str, url: str, early_text: str) -> str:
    if kind == "AR":
        m = re.search(r"fy\s*(\d{2,4})", link_text, re.I)
        if m:
            return f"FY{int(m.group(1)) % 100:02d}"
        years = [int(x) for x in re.findall(r"(20\d{2})", link_text)]
        if years:
            return f"FY{max(years) % 100:02d}"
    q = _quarter_label(link_text) or _quarter_label(early_text or "") or _quarter_from_call_date(early_text or "")
    if q:
        return q
    return "u" + hashlib.sha1(url.encode()).hexdigest()[:4]  # honest unknown


# ---------- extraction + chunking ----------

def page_texts(path: Path) -> tuple[list[tuple[int, str]], str]:
    doc = pymupdf.open(path)
    pages = [(i + 1, p.get_text("text")) for i, p in enumerate(doc)]
    p1 = pages[0][1] if pages else ""
    doc.close()
    return pages, p1


def _split_buffer(buf: str) -> list[str]:
    """Split a page's text into target-sized chunks at line boundaries."""
    if len(buf) < 120:
        return []
    out, cur = [], ""
    for line in buf.splitlines():
        if len(cur) + len(line) > CHUNK_TARGET and len(cur) >= CHUNK_MIN:
            out.append(cur.strip())
            cur = cur[-CHUNK_OVERLAP:] + " " + line
        else:
            cur = (cur + "\n" + line) if cur else line
        while len(cur) > 4200:  # pathological single line
            out.append(cur[:4200].strip())
            cur = cur[4200 - CHUNK_OVERLAP:]
    if cur.strip() and (len(cur) >= CHUNK_MIN or not out):
        out.append(cur.strip())
    elif cur.strip() and out and len(cur) > 300:
        out[-1] += "\n" + cur.strip()
    return [c for c in out if len(c) > 150]


def _heading_section(lines: list[str]) -> str | None:
    for ln in lines:
        s = ln.strip().lower()
        if not s or len(s) > 90:
            continue
        for hint, kws in AR_SECTIONS:
            if any(kw in s for kw in kws):
                return hint
    return None


def _page_section(text: str) -> str | None:
    low = text.lower()
    for hint, kws in AR_SECTIONS:
        if any(kw in low for kw in kws):
            return hint
    return None


def chunk_ar(pages: list[tuple[int, str]]):
    """Only pages under (or hitting) the sections we use. Heading tracking + keyword fallback."""
    out, counts, current = [], {}, None
    for page, text in pages:
        lines = text.splitlines()
        h = _heading_section(lines)
        if h:
            current = h
        hint = current or _page_section(text)
        if not hint:
            continue
        if counts.get(hint, 0) >= MAX_CHUNKS_PER_SECTION:
            continue
        for c in _split_buffer(text):
            if len(out) >= MAX_CHUNKS_PER_AR:
                return out
            out.append((page, hint, c))
            counts[hint] = counts.get(hint, 0) + 1
    return out


def chunk_call(pages: list[tuple[int, str]]):
    out = []
    for page, text in pages:
        for c in _split_buffer(text):
            out.append((page, "transcript", c))
    return out


# ---------- ingest one doc ----------

def ingest_doc(conn, symbol, row, kind: str) -> tuple[str, int]:
    """Returns (status, n_chunks). status: downloaded|cached|skipped:<reason>."""
    url = row["url"]

    if FINN_MODE == "snapshot":
        path = DOCS_RAW / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
        dl_status = "cached" if path.exists() else None
        if not path.exists():
            _log(conn, row, (symbol, "skipped", "snapshot: pdf not in cache", 0,
                             datetime.datetime.utcnow().isoformat()))
            return "skipped:snapshot: pdf not in cache", 0
    else:
        path, dl_status = download_pdf(url)
        if path is None:
            _log(conn, row, (symbol, "skipped", dl_status, 0,
                             datetime.datetime.utcnow().isoformat()))
            return f"skipped:{dl_status.removeprefix('error:')}", 0

    try:
        pages, p1 = page_texts(path)
    except Exception as e:
        reason = f"unreadable pdf ({type(e).__name__})"
        _log(conn, row, (symbol, "skipped", f"error:{reason}", 0,
                         datetime.datetime.utcnow().isoformat()))
        return f"skipped:{reason}", 0

    total_chars = sum(len(t) for _, t in pages)
    if total_chars < 500:
        reason = "no text layer (scanned pdf)"
        _log(conn, row, (symbol, "skipped", reason, 0, datetime.datetime.utcnow().isoformat()))
        return f"skipped:{reason}", 0

    early = "\n".join(t for _, t in pages[:3])
    period = period_label(kind, row["period"] or "", url, early)
    prefix = ("AR", "CONCALL")[kind == "CONCALL"] + "-" + period
    pieces = chunk_call(pages) if kind == "CONCALL" else chunk_ar(pages)

    per_page: dict[int, int] = {}
    now = datetime.datetime.utcnow().isoformat()
    for page, hint, text in pieces:
        n = per_page.get(page, 0)
        per_page[page] = n + 1
        chunk_id = f"{prefix}-p{page}" + (f"c{n + 1}" if n else "")
        conn.execute(
            """INSERT OR REPLACE INTO doc_chunks(symbol, chunk_id, doc_id, page, doc_kind, period, section_hint, text)
               VALUES (?,?,?,?,?,?,?,?)""",
            (symbol, chunk_id, row["id"], page, kind, period, hint, text),
        )
    n = len(pieces)
    conn.execute(
        "UPDATE docs SET path=?, text_hash=?, n_pages=? WHERE id=?",
        (str(path), hashlib.sha1(path.read_bytes()).hexdigest()[:16], len(pages), row["id"]),
    )
    _log(conn, row, (symbol, dl_status, f"{len(pages)}p text {total_chars // 1000}k", n, now))
    return dl_status, n


def _log(conn, row, log):
    conn.execute(
        """INSERT OR REPLACE INTO doc_ingest_log(doc_id, symbol, status, reason, n_chunks, fetched_at)
           VALUES (?,?,?,?,?,?)""",
        (row["id"], *log),
    )
    conn.commit()


# ---------- BM25 retrieval ----------

def _tokenize(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1 and w not in STOPWORDS]


_index_cache: dict[str, tuple] = {}


def _load_index(conn, symbol, kinds=None):
    q = "SELECT chunk_id, page, doc_kind, period, section_hint, text FROM doc_chunks WHERE symbol=?"
    params: list = [symbol]
    if kinds:
        q += f" AND doc_kind IN ({','.join('?' * len(kinds))})"
        params += list(kinds)
    rows = conn.execute(q, params).fetchall()
    docs = [_tokenize(r["text"]) for r in rows]
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi(docs) if docs else None
    _index_cache[(symbol, tuple(kinds or []))] = (rows, bm25)


def retrieve(symbol: str, query: str, k: int = 5, kinds=None) -> list[dict]:
    """BM25 over this company's chunks. kinds: e.g. ('AR',) or None for all."""
    key = (symbol, tuple(kinds) if kinds else ())
    if key not in _index_cache:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        _load_index(conn, symbol, kinds)
        conn.close()
    rows, bm25 = _index_cache[key]
    if not bm25:
        return []
    scores = bm25.get_scores(_tokenize(query))
    top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    out = []
    for i in top:
        if scores[i] <= 0:
            continue
        r = rows[i]
        out.append({
            "chunk_id": r["chunk_id"], "page": r["page"], "doc_kind": r["doc_kind"],
            "period": r["period"], "section_hint": r["section_hint"],
            "score": round(float(scores[i]), 2), "text": r["text"],
        })
    return out


# ---------- screener ensure ----------

def ensure_screener(symbol: str, refresh: bool):
    conn = sqlite3.connect(DB_PATH)
    n_fund = conn.execute("SELECT COUNT(*) FROM screener_fundamentals WHERE symbol=?", (symbol,)).fetchone()[0]
    n_docs = conn.execute("SELECT COUNT(*) FROM docs WHERE symbol=?", (symbol,)).fetchone()[0]
    conn.close()
    if refresh or n_fund == 0 or n_docs == 0:
        if refresh:
            for f in (RAW_DIR / "screener").glob(f"{symbol}_*.html"):
                f.unlink(missing_ok=True)
        print(f"screener: fetching ({symbol}) ...")
        screener.run([symbol])
    else:
        print(f"screener: cached ({n_fund} fund rows, {n_docs} doc links)")


# ---------- main ----------

def run_symbol(symbol: str, refresh: bool = False, demo_query: str | None = None) -> dict:
    symbol = symbol.upper()
    ensure_screener(symbol, refresh)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)

    # clear stale chunks for this symbol on refresh
    if refresh:
        conn.execute("DELETE FROM doc_chunks WHERE symbol=?", (symbol,))
        conn.commit()

    ars, calls = select_docs(conn, symbol)
    stats = {"symbol": symbol, "ar": len(ars), "calls": len(calls),
             "downloaded": 0, "cached": 0, "skipped": [], "chunks": {"AR": 0, "CONCALL": 0}}

    for row in ars + calls:
        kind = "CONCALL" if _transcript(row) else "AR"
        status, n = ingest_doc(conn, symbol, row, kind)
        stats["chunks"][kind] += n
        if status in ("downloaded", "cached"):
            stats[status] += 1
        else:
            stats["skipped"].append(f"{kind}-{(row['period'] or '')[:38]}: {status.removeprefix('skipped:')}")
        print(f"  [{kind}] {(row['period'] or '')[:60]} -> {status} ({n} chunks)")

    conn.commit()
    conn.close()

    # acceptance demo: one retrieve() per company
    q = demo_query or "margin outlook guidance revenue growth"
    hits = retrieve(symbol, q, k=3)
    print(f"  retrieve({symbol!r}, {q!r}) -> {len(hits)} hits")
    for h in hits:
        print(f"    {h['chunk_id']} ({h['section_hint']}, score {h['score']}): {h['text'][:150]!r}")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    stats = run_symbol(args.symbol, args.refresh)
    n_skipped = len(stats["skipped"])
    print(f"\n{stats['symbol']}: ARs found={stats['ar']} transcripts found={stats['calls']} "
          f"downloaded={stats['downloaded']} cached={stats['cached']} skipped={n_skipped}")
    for s in stats["skipped"]:
        print(f"  skip: {s}")
    print(f"  chunks: AR={stats['chunks']['AR']} CONCALL={stats['chunks']['CONCALL']}")
    print(f"  db size: {DB_PATH.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
