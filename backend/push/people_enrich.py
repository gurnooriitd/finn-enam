"""
Push — management-change enrichment (local batch, cached into the DB).

Two LLM steps via the gateway (cheap tier, pydantic-validated, gateway-cached):
  1. facts extraction from the filing text only (subject + voiceSummary, plus
     the attachment PDF when the visible text lacks person/role/date);
  2. a 2-3 line background for INCOMING people, written ONLY from web-search
     snippets (ddgs, no key, no LinkedIn scraping). Ambiguous/empty -> bio
     "background not verified". Never guesses.

Runs offline-batch via scripts/enrich_people.py; the app only reads the table.
"""
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

from backend.config import DB_PATH, BASE_DIR, LLM_ENABLED

DOCS_DIR = BASE_DIR / "data" / "raw" / "docs"
ACTIONS = ("appointed", "re-appointed", "resigned", "ceased", "retired")


class MgmtFacts(BaseModel):
    person: str | None = None
    designation: str | None = None
    action: str | None = None
    effective_date: str | None = None
    reason: str | None = None
    predecessor: str | None = None


class BioOut(BaseModel):
    bio: str | None = None
    confidence: str  # high | medium | low
    sources: list[str] = []


def ensure_table(conn: sqlite3.Connection):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS people_enrichment (
            announcement_id TEXT PRIMARY KEY,
            symbol TEXT,
            person TEXT,
            designation TEXT,
            action TEXT,
            effective_date TEXT,
            reason TEXT,
            predecessor TEXT,
            bio TEXT,
            confidence TEXT,
            sources_json TEXT,
            created_at TEXT
        )"""
    )
    conn.commit()


def pdf_text(url: str, max_pages: int = 3) -> str:
    """Attachment text, cached under data/raw/docs/ by URL hash. '' if unavailable."""
    import requests

    if not url:
        return ""
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    cache_txt = DOCS_DIR / f"ann_{h}.txt"
    if cache_txt.exists():
        return cache_txt.read_text(errors="ignore")
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (FINN research)"})
        r.raise_for_status()
        import fitz
        doc = fitz.open(stream=r.content, filetype="pdf")
        text = "\n".join(p.get_text() for p in doc[:max_pages])
        doc.close()
    except Exception:
        return ""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    cache_txt.write_text(text or "")
    return text or ""


def _llm_facts(subject: str, summary: str, attachment: str, symbol: str) -> MgmtFacts:
    from backend.llm.gateway import call_llm

    ctx = f"Subject: {subject}\nSummary: {summary}"
    if attachment:
        ctx += f"\n\nFiling text (extract):\n{attachment[:4000]}"
    out = call_llm(
        [
            {"role": "system", "content":
                "You extract management-change facts from Indian stock-exchange filings. "
                "Use ONLY the text provided; if a field is not stated there, use null. "
                "action must be exactly one of: appointed, re-appointed, resigned, ceased, retired. "
                "Dates normalised to 'DD Mon YYYY'."},
            {"role": "user", "content": f"Company {symbol}. Filing:\n{ctx}"},
        ],
        tier="cheap", step="people_extract", response_schema=MgmtFacts,
    )
    return out


def _web_hits(person: str, designation: str | None, company: str) -> list[dict]:
    """ddgs search, no key, no LinkedIn scraping. Top 5 deduped by URL."""
    from ddgs import DDGS

    q1 = " ".join(x for x in (f'"{person}"', f'"{designation}"' if designation else "", f'"{company}"') if x)
    queries = [q1, f'"{person}" {company} linkedin']
    hits, seen = [], set()
    for q in queries:
        for attempt in range(2):  # ddgs backends time out intermittently
            try:
                for r in DDGS(timeout=25).text(q, region="in-en", max_results=6):
                    u = r.get("href") or r.get("url") or ""
                    if not u or u in seen or "linkedin.com/in" in u:
                        continue
                    seen.add(u)
                    hits.append({"title": (r.get("title") or "")[:120], "url": u,
                                 "snippet": (r.get("body") or "")[:400]})
                break
            except Exception:
                time.sleep(2)
    return hits[:5]


def _llm_bio(person: str, designation: str | None, company: str, hits: list[dict]) -> BioOut:
    from backend.llm.gateway import call_llm

    if not hits:
        return BioOut(bio=None, confidence="low", sources=[])
    ctx = "\n\n".join(f"[{i+1}] {h['title']} — {h['url']}\n{h['snippet']}" for i, h in enumerate(hits))
    out = call_llm(
        [
            {"role": "system", "content":
                "Write a 2-3 line background for the named executive using ONLY facts present "
                "in the numbered search snippets (prior roles, companies, education). "
                "Cite snippet numbers like [1] after each claim. "
                "If the snippets do not clearly describe THIS person (same name, same company/role), "
                "or say nothing relevant, set bio to null and confidence to 'low'. Never guess."},
            {"role": "user", "content":
                f"Person: {person}\nDesignation: {designation or 'n/a'}\nCompany: {company}\n\nSnippets:\n{ctx}"},
        ],
        tier="cheap", step="people_bio", response_schema=BioOut,
    )
    out.sources = [{"title": h["title"], "url": h["url"], "snippet": h["snippet"]} for h in hits][:5]
    if (out.confidence or "low").lower() not in ("high", "medium"):
        out.bio = None
        out.confidence = "low"
    return out


def mgmt_window_rows(conn: sqlite3.Connection, days: int = 30) -> list[sqlite3.Row]:
    as_of = conn.execute("SELECT MAX(ts_iso) FROM announcements").fetchone()[0]
    cutoff = (datetime.fromisoformat(as_of[:10]) - timedelta(days=days)).isoformat()
    return conn.execute(
        "SELECT * FROM announcements WHERE category='management_change' AND ts_iso >= ? ORDER BY ts_iso",
        (cutoff,),
    ).fetchall()


def run(days: int = 30, force: bool = False) -> list[dict]:
    assert LLM_ENABLED, "LLM_ENABLED=0 — run locally, not in snapshot mode"
    from backend.push.context import company_name
    from backend.push.people import extract_mgmt_change

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    done = {r[0] for r in conn.execute("SELECT announcement_id FROM people_enrichment")}
    rows = mgmt_window_rows(conn, days)
    results = []
    for r in rows:
        if r["id"] in done and not force:
            row = conn.execute(
                "SELECT * FROM people_enrichment WHERE announcement_id=?", (r["id"],)
            ).fetchone()
            results.append({"symbol": r["symbol"], "id": r["id"], "person": row["person"],
                            "action": row["action"], "confidence": row["confidence"],
                            "n_sources": len(json.loads(row["sources_json"] or "[]")), "cached": True})
            continue
        company = company_name(r["symbol"])
        regex = extract_mgmt_change(r["subject"] or "", r["voice_summary"] or "", r["raw_desc"] or "") or {}
        need_pdf = not (regex.get("person") and regex.get("role") and regex.get("effective_date"))
        attachment = pdf_text(r["attachment_url"] or "") if need_pdf else ""
        facts = _llm_facts(r["subject"] or "", r["voice_summary"] or "", attachment, r["symbol"])
        person = facts.person or regex.get("person")
        designation = facts.designation or regex.get("role")
        action = (facts.action or regex.get("action") or "").lower().replace("re-appointed", "re-appointed")
        if action not in ACTIONS:
            action = regex.get("action") or None

        bio, confidence, sources = None, None, []
        if person and action in ("appointed", "re-appointed"):
            hits = _web_hits(person, designation, company)
            out = _llm_bio(person, designation, company, hits)
            bio = (out.bio or "").strip() or None
            confidence = out.confidence
            sources = out.sources
            if bio is None:
                bio = "background not verified"

        conn.execute(
            "INSERT OR REPLACE INTO people_enrichment VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["symbol"], person, designation, action, facts.effective_date or regex.get("effective_date"),
             facts.reason, facts.predecessor, bio, confidence, json.dumps(sources),
             datetime.utcnow().isoformat()),
        )
        conn.commit()
        results.append({"symbol": r["symbol"], "id": r["id"], "person": person, "action": action,
                        "confidence": confidence, "n_sources": len(sources), "cached": False})
    conn.close()
    return results


def domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")
