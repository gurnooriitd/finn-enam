"""BM25 over doc_chunks. No PDF libs — safe to import inside the snapshot app."""
import re
import sqlite3

from backend.config import DB_PATH

STOPWORDS = set(
    "the a an of and or to in for on with by is are was were be as at from that this "
    "it its we our you your they their he she his her not no yes if but so than then "
    "will would shall should can could may might have has had do does did also more "
    "most other such only each any all per vs".split()
)

_cache: dict[tuple, tuple] = {}


def _tokenize(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 1 and w not in STOPWORDS]


def retrieve(symbol: str, query: str, k: int = 4) -> list[dict]:
    symbol = (symbol or "").upper()
    key = symbol
    if key not in _cache:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT chunk_id, page, doc_kind, period, section_hint, text "
                "FROM doc_chunks WHERE symbol=?",
                (symbol,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()
        docs = [_tokenize(r["text"] or "") for r in rows]
        bm25 = None
        if docs:
            from rank_bm25 import BM25Okapi
            bm25 = BM25Okapi(docs)
        _cache[key] = (rows, bm25)
    rows, bm25 = _cache[key]
    if not bm25 or not query.strip():
        return []
    scores = bm25.get_scores(_tokenize(query))
    top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    out = []
    for i in top:
        if scores[i] <= 0:
            continue
        r = rows[i]
        text = re.sub(r"\s+", " ", r["text"] or "").strip()
        out.append({
            "chunk_id": r["chunk_id"],
            "page": r["page"],
            "doc_kind": r["doc_kind"],
            "period": r["period"],
            "section_hint": r["section_hint"],
            "score": round(float(scores[i]), 2),
            "text": text[:700],
        })
    return out
