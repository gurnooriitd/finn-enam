"""
Push — Classifier
Rule-based first (category from NSE desc field + keyword/regex rules).
Only ambiguous items go to the cheap LLM.
Cached by text hash in SQLite.
"""
import hashlib
import json
import re
import sqlite3
from typing import Optional

from pydantic import BaseModel

from backend.config import DB_PATH

# Subject-first rules. NSE's `desc` field IS the subject line; these regexes
# are applied to the subject BEFORE anything else. Order matters: results and
# meet/call rules run before the generic "press release"/"updates" catch-alls.
SUBJECT_RULES = [
    # results — only when the subject itself says so
    (r"financial result|quarterly result|annual result|half.?yearly result", "results"),
    # "Outcome of Board Meeting" -> results only when it mentions financial results
    # (NSE subjects are terse; the mention usually lives in the filing body)
    (r"outcome of board meeting", "__board_outcome__"),
    # meets / calls: default investor_meetings; earnings_call when the subject
    # signals a transcript / audio recording / call schedule
    (r"con\.?\s*call|conference call|earnings call", "__call_or_meet__"),
    (r"analysts?/institutional investor|investor meet|institutional investor|analyst meet|investor presentation|non-deal road", "investor_meetings"),
    # press release, unless already claimed by a results rule above
    (r"press release|media release", "press_release"),
    # "General Updates" / bare "Updates" -> unclassified unless an earlier
    # subject rule matched (i.e. reached only if nothing else matched)
    (r"^general updates$|^updates$", "unclassified"),
    (r"credit rating", "credit_rating"),
    (r"dividend", "dividend"),
    (r"bonus|stock split|sub.?division", "bonus_split"),
    (r"merger|amalgamat|demerger|acquisition|scheme of arrangement|restructur|takeover|sale or disposal", "M&A"),
    # Routine share paperwork — near-zero base, must beat the fund_raise
    # catch-all below (ESOP allotments are NOT fund raises).
    (r"\besop\b|\besos\b|\besps\b", "unclassified"),
    (r"trading window", "unclassified"),
    (r"certificate under|compliance certificate|duplicate (?:share|equity) certificate", "unclassified"),
    (r"allotment|issue of securities|rights issue|qip|\bncd\b|preferential|fund rais|warrant", "fund_raise"),
    (r"change in director|change in management|resignation|appointment|retirement|cessation|remuneration|re-appointment", "management_change"),
    (r"pledge|encumbrance", "pledging"),
    (r"insider|bulk deal|block deal|sast", "insider_trading"),
    (r"action\(s\) taken|orders passed|penalt|litigation|arbitrat|adjudicat|sebi order|nclt|insolvency|winding", "litigation"),
    (r"capex|capital expenditure|commissioning", "capex"),
    (r"\bagm\b|\begm\b|extra.?ordinary general|shareholders meeting|postal ballot|court convened", "agm_egm"),
    (r"bagging|receiving of order|winning of order|letter of intent|work order", "capex"),
    (r"newspaper publication|news verification|rumour verification|certificate under sebi", "unclassified"),
]

# Body/voiceSummary keyword rules — used ONLY when the subject yields nothing.
# NOTE: no "results" pattern here on purpose; the word "results" in a filing
# body or voice summary must never turn a non-results filing into results.
KEYWORD_RULES = [
    (r"\bresign(ation|ed)?\b", "management_change"),
    (r"\bappoint(ment|ed)?\b|\bretire(ment|d)\b|\bcessation\b", "management_change"),
    (r"\bceo\b|\bcfo\b|\bmd\b|\bcoo\b|\bmanaging director\b|\bchief executive\b", "management_change"),
    (r"\bmerger\b|\bacquisition\b|\bdemerger\b|\bamalgamat", "M&A"),
    (r"\bbonus share\b|\bstock split\b|\bsub.?division\b", "bonus_split"),
    (r"\bdividend\b|\bfinal div\b|\binterim div\b", "dividend"),
    (r"\bcrisil\b|\bicra\b|\bindia rating\b|\bfitch\b|\bmoody\b|\bdowngrade\b|\bcredit rating\b", "credit_rating"),
    (r"\bqip\b|\bncd\b|\bdebenture\b|\brights issue\b|\bfpo\b|\bpreferential\b|\ballotment\b", "fund_raise"),
    (r"\bpledge\b|\bencumber\b", "pledging"),
    (r"\binsider\b|\bbulk deal\b|\bblock deal\b|\bsast\b", "insider_trading"),
    (r"\bpenalt(y|ies)\b|\blitigation\b|\barbitrat\b|\badjudicat\b|\bsebi order\b|\bnclt\b|\baction.*taken.*order", "litigation"),
    (r"\bcapex\b|\bcapital expenditure\b|\bcommission(ed|ing)\b.*plant", "capex"),
    (r"\bagm\b|\begm\b|\bextra.ordinary general\b", "agm_egm"),
    (r"\bpostal ballot\b", "postal_ballot"),
    (r"\btranscript\b|\baudio recording\b|\bearnings call\b", "earnings_call"),
    (r"\binvestor meet\b|\binvestor presentation\b|\banalyst meet\b", "investor_meetings"),
    (r"\bpress release\b|\bmedia release\b", "press_release"),
]


class CategoryResult(BaseModel):
    category: str
    confidence: float


def _subject_category(subject: str, body: str = "") -> Optional[str]:
    """Apply SUBJECT_RULES to the subject line. NSE's desc field is the subject."""
    s = (subject or "").lower().strip()
    if not s:
        return None
    for pattern, cat in SUBJECT_RULES:
        if re.search(pattern, s, re.IGNORECASE | re.DOTALL):
            if cat == "__call_or_meet__":
                # Con. Call: earnings_call when the subject signals a
                # transcript / audio recording / schedule, else investor_meetings
                if re.search(r"transcript|audio|schedule|intimation", s):
                    return "earnings_call"
                return "investor_meetings"
            if cat == "__board_outcome__":
                if re.search(r"financial result|quarterly result|annual result", body or "", re.IGNORECASE):
                    return "results"
                return None
            return cat
    return None


def rule_classify(subject: str, desc: str, voice_summary: str = "") -> Optional[str]:
    """Subject first; body text and voice summary only as a last resort.

    `desc` here is NSE's attchmntText (the filing body). The word "results" in
    a body or voice summary can never override a subject-based rule because
    KEYWORD_RULES deliberately has no results pattern.
    """
    # 1. Subject (NSE desc field / subject column)
    body = (desc or "").lower()
    cat = _subject_category(subject, body)
    if cat:
        return cat

    # 2. Body text (attchmntText) — last resort before voice summary
    if body:
        for pattern, cat in KEYWORD_RULES:
            if re.search(pattern, body, re.IGNORECASE):
                return cat

    # 3. Voice summary — final fallback, same keyword set (no results rule)
    voice = (voice_summary or "").lower()
    if voice:
        for pattern, cat in KEYWORD_RULES:
            if re.search(pattern, voice, re.IGNORECASE):
                return cat

    return None


def llm_classify(subject: str, desc: str, voice_summary: str = "") -> CategoryResult:
    from backend.llm.gateway import call_llm

    text = f"Subject: {subject}\nCategory hint: {desc}\nSummary: {voice_summary}"[:800]
    messages = [
        {"role": "system", "content": (
            "Classify this Indian stock exchange announcement into exactly one category.\n"
            "Categories: results, M&A, fund_raise, management_change, litigation, insider_trading, "
            "earnings_call, investor_meetings, credit_rating, dividend, bonus_split, capex, "
            "press_release, postal_ballot, agm_egm, pledging, unclassified\n"
            "Respond with JSON: {\"category\": \"<cat>\", \"confidence\": <0.0-1.0>}"
        )},
        {"role": "user", "content": text},
    ]
    return call_llm(messages, tier="cheap", step="classify",
                    response_schema=CategoryResult, max_retries=2)


def classify_announcement(ann_id: str, subject: str, desc: str, voice_summary: str = "") -> str:
    """Returns category string. Rules first; LLM only for ambiguous and only
    when LLM_ENABLED — never blocks ingestion."""
    from backend.config import LLM_ENABLED

    cat = rule_classify(subject, desc, voice_summary)
    if cat:
        return cat

    if not LLM_ENABLED:
        return "unclassified"
    try:
        result = llm_classify(subject, desc, voice_summary)
        return result.category
    except Exception:
        return "unclassified"


def run_classify_all(force: bool = False):
    """Classify announcements in DB. force=True reclassifies every row
    (used when rules change); default only fills NULLs."""
    conn = sqlite3.connect(DB_PATH)
    if force:
        conn.execute("UPDATE announcements SET category=NULL")
    rows = conn.execute(
        "SELECT id, subject, raw_desc, voice_summary FROM announcements WHERE category IS NULL"
    ).fetchall()

    for row in rows:
        cat = classify_announcement(row[0], row[1] or "", row[2] or "", row[3] or "")
        conn.execute("UPDATE announcements SET category=? WHERE id=?", (cat, row[0]))

    conn.commit()
    conn.close()
    print(f"Classified {len(rows)} announcements")
