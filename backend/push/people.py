"""
Push — Management-change extraction (deterministic, no LLM).
Regex over subject / voiceSummary / filing body. Returns None when the
person or action can't be found — the UI then shows only the clamped
summary. Never invents a name or date.
"""
import re
import urllib.parse

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NUM = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}

# "Mr. V. Srinivasa Rangan", "Shri Shrirang Anilrao Kothe", "Mr. Noel Naval Tata"
_NAME_TOKEN = r"(?:[A-Z]\.|[A-Z][a-zA-Z]+)"
PERSON_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Shri|Smt)\.?\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})"
)

ACTIONS = [
    (r"re-?\s?appoint", "re-appointed"),
    (r"resign", "resigned"),
    (r"retire", "retired"),
    (r"cessation|ceased to be|ceased", "ceased"),
    (r"appoint", "appointed"),
]

# "as Whole-time Director designated as Executive Director for...",
# "as CEO Helicopter Complex.", "as Non-Executive, Non-Independent Director and Vice Chairman effective..."
ROLE_STOP_RE = re.compile(
    r"\b(?:effective|w\.?e\.?f\.?|with effect|for a period|and is|who)\b|[.;]",
    re.I,
)
ROLE_ANCHOR_RE = re.compile(
    r"\b(?:as|designated as|promoted as|elevated as)\s+", re.I
)

EFFECTIVE_RE = re.compile(
    r"(?:effective(?: from)?|with effect from|w\.?e\.?f\.?|"
    r"effective date[^.;]{0,40}?\b(?:is|:|be)\s*)\s*"
    r"((?:\d{1,2}(?:st|nd|rd|th)?\s+)?[A-Za-z]{3,9},?\s+\d{4}"
    r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.I,
)


def _clean_role(frag: str) -> str | None:
    frag = re.split(r"\s+designated\s+as\s+", frag, flags=re.I)[0]
    frag = frag.strip(" ,").strip()
    # Drop filler from NSE subjects: "... as the Non-Executive ... of the Board"
    frag = re.sub(r"^(?:the\s+)", "", frag, flags=re.I)
    frag = re.sub(r"\s+of\s+the\s+(?:company|board)$", "", frag, flags=re.I)
    if not frag or len(frag) > 64 or len(frag) < 2:
        return None
    return frag


def _extract_role(text: str) -> str | None:
    for m in ROLE_ANCHOR_RE.finditer(text):
        end_m = ROLE_STOP_RE.search(text, m.end())
        end = end_m.start() if end_m else min(m.end() + 80, len(text))
        role = _clean_role(text[m.end():end])
        if role:
            return role
    return None


def _fmt_date(raw: str) -> str:
    """Normalise 'November 23, 2026' / '13th November 2026' / '23-11-2026'."""
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9}),?\s+(\d{4})", raw)
    if m:
        mon = MONTH_NUM.get(m.group(2).lower()[:3])
        if mon:
            return f"{int(m.group(1))} {MONTHS[mon - 1]} {m.group(3)}"
        return raw
    m = re.match(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})", raw)
    if m:
        mon = MONTH_NUM.get(m.group(1).lower()[:3])
        if mon:
            return f"{int(m.group(2))} {MONTHS[mon - 1]} {m.group(3)}"
        return raw
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", raw)
    if m:
        return f"{int(m.group(1))} {MONTHS[int(m.group(2)) - 1]} {m.group(3)}"
    return raw


def extract_mgmt_change(subject: str, voice_summary: str = "", body: str = "") -> dict | None:
    text = " ".join(x for x in (subject, voice_summary, body) if x).strip()
    if not text:
        return None

    action = None
    for pat, label in ACTIONS:
        if re.search(pat, text, re.I):
            action = label
            break
    if action is None:
        return None

    pm = PERSON_RE.search(text)
    if not pm:
        return None
    person = pm.group(1).strip().rstrip(".,")

    role = _extract_role(text)

    effective = None
    em = EFFECTIVE_RE.search(text)
    if em:
        effective = _fmt_date(em.group(1).strip())

    return {
        "person": person,
        "role": role,
        "action": action,
        "effective_date": effective,
    }


def lookup_url(person: str, company: str, role: str | None) -> str:
    q = " ".join(x for x in (f'"{person}"', f'"{company}"' if company else "", f'"{role}"' if role else "") if x)
    return "https://www.google.com/search?q=" + urllib.parse.quote(q)
