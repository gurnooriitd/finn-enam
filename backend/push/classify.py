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

# Map NSE `desc` category strings to our categories
NSE_DESC_MAP = {
    "results": "results",
    "financial results": "results",
    "quarterly results": "results",
    "half yearly results": "results",
    "annual results": "results",
    "board meeting": "results",
    "mergers / acquisitions / restructuring": "M&A",
    "amalgamation": "M&A",
    "demerger": "M&A",
    "acquisition": "M&A",
    "takeover": "M&A",
    "fund raising": "fund_raise",
    "rights issue": "fund_raise",
    "ncd": "fund_raise",
    "ipo": "fund_raise",
    "qip": "fund_raise",
    "fpo": "fund_raise",
    "change in management": "management_change",
    "appointment": "management_change",
    "resignation": "management_change",
    "change in directorate": "management_change",
    "credit rating": "credit_rating",
    "dividend": "dividend",
    "bonus": "bonus_split",
    "stock split": "bonus_split",
    "split": "bonus_split",
    "sub division": "bonus_split",
    "capital expenditure": "capex",
    "capex": "capex",
    "press release": "press_release",
    "investor presentation": "investor_meetings",
    "investor meet": "investor_meetings",
    "institutional investor": "investor_meetings",
    "analyst meet": "investor_meetings",
    "analysts/institutional investor": "investor_meetings",
    "earnings call": "earnings_call",
    "concall": "earnings_call",
    "conference call": "earnings_call",
    "agm": "agm_egm",
    "egm": "agm_egm",
    "postal ballot": "postal_ballot",
    "notice": "postal_ballot",
    "pledging": "pledging",
    "encumbrance": "pledging",
    "insider trading": "insider_trading",
    "bulk deal": "insider_trading",
    "block deal": "insider_trading",
    "litigation": "litigation",
    "legal": "litigation",
    "penalty": "litigation",
    "notice from sebi": "litigation",
}

KEYWORD_RULES = [
    (r"\bresign(ation|ed)?\b", "management_change"),
    (r"\bappoint(ment|ed)?\b", "management_change"),
    (r"\bceo\b|\bcfo\b|\bmd\b|\bcoo\b|\bmanaging director\b|\bchief executive\b", "management_change"),
    (r"\bmerger\b|\bacquisition\b|\bdemerger\b|\bamalgamat", "M&A"),
    (r"\bbonus share\b|\bstock split\b|\bsub.?division\b", "bonus_split"),
    (r"\bdividend\b|\bfinal div\b|\binterim div\b", "dividend"),
    (r"\brating\b|\bcrisil\b|\bicra\b|\bcare\b|\bindia rating\b|\bfitch\b|\bmoody\b|\bdowngrade\b", "credit_rating"),
    (r"\bqip\b|\bncd\b|\bdebenture\b|\brights issue\b|\bfpo\b|\bpreferential\b", "fund_raise"),
    (r"\bpleas(e|ing)\b|\bpledge\b|\bencumber\b|\bpromot(er|ors).*?pledge", "pledging"),
    (r"\binsider\b|\bbulk deal\b|\bblock deal\b|\bsast\b|\bsebi.*acqui", "insider_trading"),
    (r"\bpenalt(y|ies)\b|\blitigation\b|\barbitrat\b|\badjudicat\b|\bsebi order\b|\bnclt\b|\bnotice.*sebi\b", "litigation"),
    (r"\bcapex\b|\bcapital expenditure\b|\bcommission(ed|ing)\b.*plant", "capex"),
    (r"\bagm\b|\begm\b|\bextra.ordinary general\b", "agm_egm"),
    (r"\bpostal ballot\b|\bpostal.?ballot\b", "postal_ballot"),
    (r"\bearnings call\b|\bconcall\b|\bconference call\b|\banalyst\b", "earnings_call"),
    (r"\binvestor meet\b|\binvestor presentation\b|\banalyst meet\b", "investor_meetings"),
    (r"\bpress release\b|\bmedia release\b", "press_release"),
    (r"\bquarterly result\b|\bhalf.?year\b|\bannual result\b|\bfinancial result\b", "results"),
]


class CategoryResult(BaseModel):
    category: str
    confidence: float


def rule_classify(subject: str, desc: str, voice_summary: str = "") -> Optional[str]:
    """Returns category string or None if ambiguous."""
    text = f"{subject} {desc}".lower()

    # Try NSE desc field first (most reliable)
    desc_lower = desc.lower().strip()
    for key, cat in NSE_DESC_MAP.items():
        if key in desc_lower:
            return cat

    # Keyword/regex rules
    for pattern, cat in KEYWORD_RULES:
        if re.search(pattern, text, re.IGNORECASE):
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


def run_classify_all():
    """Classify all uncategorized announcements in DB."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, subject, raw_desc, voice_summary FROM announcements WHERE category IS NULL"
    ).fetchall()

    for row in rows:
        cat = classify_announcement(row[0], row[1] or "", row[2] or "", row[3] or "")
        conn.execute("UPDATE announcements SET category=? WHERE id=?", (cat, row[0]))

    conn.commit()
    conn.close()
    print(f"Classified {len(rows)} announcements")
