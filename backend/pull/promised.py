"""Promised vs Delivered.

Extractions live in guidance_items (written by scripts/extract_guidance.py).
This module only resolves them: the target numbers are parsed from the
verbatim quote, and the actuals are computed from screener tables.
Nothing here calls a model or the network.
"""
import re
import sqlite3

from backend.config import DB_PATH

MONTH = {1: "Jun", 2: "Sep", 3: "Dec", 4: "Mar"}
MONTH_NUM = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
             "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

# Phrases management actually says. The numeric band is a labelled reading
# of the words, never a silent substitution.
PHRASES = [
    (r"high single[- ]digit", 7.0, 9.0),
    (r"mid[- ]single[- ]digit", 4.0, 6.0),
    (r"low single[- ]digit", 1.0, 3.0),
    (r"single[- ]digit", 1.0, 9.0),
    (r"low double[- ]digit|low teens", 10.0, 13.0),
    (r"mid[- ]teens", 14.0, 16.0),
    (r"high teens", 17.0, 19.0),
    (r"double[- ]digit", 10.0, None),
]

METRIC_LABEL = {
    "revenue": "Revenue",
    "operating_margin": "Operating margin",
    "pat": "Net profit",
    "volume": "Volume",
    "capex": "Capex",
    "other": "Other",
}

CHECKABLE = {"revenue", "operating_margin", "pat"}


def ensure_table(conn: sqlite3.Connection | None = None):
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS guidance_items (
        symbol TEXT NOT NULL,
        call_period TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        page INTEGER,
        metric TEXT NOT NULL,
        comparison TEXT NOT NULL,
        horizon TEXT NOT NULL,
        qualifier TEXT,
        quote TEXT NOT NULL,
        speaker TEXT,
        low REAL,
        high REAL,
        interpreted INTEGER DEFAULT 0,
        created_at TEXT,
        PRIMARY KEY (symbol, call_period, chunk_id, quote)
    );
    CREATE TABLE IF NOT EXISTS guidance_runs (
        symbol TEXT NOT NULL,
        call_period TEXT NOT NULL,
        n_kept INTEGER,
        status TEXT,
        created_at TEXT,
        PRIMARY KEY (symbol, call_period)
    );
    """)
    if own:
        conn.commit()
        conn.close()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", (s or "").lower()).strip()


def quote_in_chunk(quote: str, text: str) -> bool:
    nq, nt = _norm(quote), _norm(text)
    if len(nq) < 12:
        return False
    if nq in nt:
        return True
    words = [w for w in nq.split() if len(w) > 2]
    if len(words) < 5:
        return False
    return sum(w in nt for w in words) / len(words) >= 0.85


def direction_from_quote(quote: str) -> str | None:
    q = quote.lower()
    if re.search(r"\b(compress|compression|decline|de-?grow|lower|pressure|slowdown|fall)\b", q):
        return "down"
    if re.search(r"\b(maintain|stable|flattish|unchanged)\b", q):
        return "flat"
    if re.search(r"\b(expand|expansion|improve|improvement|increase|higher)\b", q):
        return "up"
    if re.search(r"\b(expect|guide|guidance|outlook|target|aim)\b.{0,48}\b(grow|growth)\b", q):
        return "up"
    return None


def targets_from_quote(quote: str) -> dict | None:
    """Numbers come from the quote itself. The model does not get to invent them."""
    q = quote.lower().replace("–", "-").replace("—", "-")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*(?:bps|basis points)", q)
    if m:
        return {"low": float(m.group(1)) / 100, "high": float(m.group(2)) / 100,
                "unit": "pp", "interpreted": False, "direction": None}
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:bps|basis points)", q)
    if m:
        v = float(m.group(1)) / 100
        return {"low": v, "high": v, "unit": "pp", "interpreted": False, "direction": None}
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)?\s*(?:to|-|and)\s*(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)",
        q,
    )
    if m:
        return {"low": float(m.group(1)), "high": float(m.group(2)),
                "unit": "pct", "interpreted": False, "direction": None}
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)", q)
    if m:
        v = float(m.group(1))
        return {"low": v, "high": v, "unit": "pct", "interpreted": False, "direction": None}
    for pat, lo, hi in PHRASES:
        if re.search(pat, q):
            return {"low": lo, "high": hi, "unit": "pct", "interpreted": True, "direction": None}
    d = direction_from_quote(q)
    if d:
        return {"low": None, "high": None, "unit": "direction", "interpreted": False, "direction": d}
    return None


def parse_qfy(label: str):
    m = re.search(r"Q\s*([1-4])\s*FY\s*(\d{2,4})", label or "", re.I)
    if not m:
        return None
    fy = int(m.group(2))
    if fy > 100:
        fy %= 100
    return int(m.group(1)), fy


def parse_fy_only(label: str):
    if re.search(r"Q\s*[1-4]", label or "", re.I):
        return None
    m = re.search(r"FY\s*(\d{2,4})", label or "", re.I)
    if not m:
        return None
    fy = int(m.group(1))
    return fy % 100 if fy > 100 else fy


def _next_q(q, fy):
    return (1, fy + 1) if q == 4 else (q + 1, fy)


def resolve_horizon(horizon: str, call_period: str):
    """Return ('quarter', (q, fy)) | ('year', fy) | ('open', None) | ('past', None)."""
    h = (horizon or "").lower()
    call = parse_qfy(call_period)
    explicit = parse_qfy(horizon or "")
    if explicit:
        kind, spec = "quarter", explicit
    else:
        fy = parse_fy_only(horizon or "")
        vague = re.search(r"onwards|every year|few years|medium|long[- ]term|over the next", h)
        if fy and "quarter" not in h and not vague:
            kind, spec = "year", fy
        elif call and vague:
            return ("open", None)
        elif call and re.search(r"next quarter|coming quarter|subsequent quarter|quarter ahead", h):
            kind, spec = "quarter", _next_q(*call)
        elif call and re.search(r"next financial year|coming financial year|next year|coming year", h):
            kind, spec = "year", call[1] + 1
        elif call and re.search(r"full year|this year|rest of the year|fiscal year|financial year|for the year|current year", h):
            kind, spec = "year", call[1]
        else:
            return ("open", None)
    if kind == "quarter" and call:
        cq, cfy = call
        q, fy = spec
        if (fy, q) <= (cfy, cq):
            return ("past", None)
    # On the Q4 call the financial year just reported is history, not a promise.
    if kind == "year" and call and (spec < call[1] or (spec == call[1] and call[0] == 4)):
        return ("past", None)
    return (kind, spec)


def screener_quarter(q: int, fy: int) -> str:
    year = 2000 + fy if q == 4 else 2000 + fy - 1
    return f"{MONTH[q]} {year}"


def screener_year(fy: int) -> str:
    return f"Mar {2000 + fy}"


def _period_ord(label: str):
    m = re.match(r"([A-Za-z]{3}) (\d{4})", label or "")
    if not m:
        return None
    return int(m.group(2)) * 12 + MONTH_NUM.get(m.group(1), 0)


def _shift_year(label: str, years: int = -1) -> str | None:
    m = re.match(r"([A-Za-z]{3}) (\d{4})", label or "")
    if not m:
        return None
    return f"{m.group(1)} {int(m.group(2)) + years}"


def _value(conn, symbol, table, metric, period):
    row = conn.execute(
        "SELECT value FROM screener_fundamentals "
        "WHERE symbol=? AND table_type=? AND metric=? AND period=?",
        (symbol, table, metric, period),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def _latest_period(conn, symbol, table, metric) -> str | None:
    rows = conn.execute(
        "SELECT period, value FROM screener_fundamentals "
        "WHERE symbol=? AND table_type=? AND metric=? AND period!='TTM' AND value IS NOT NULL",
        (symbol, table, metric),
    ).fetchall()
    best = None
    best_ord = -1
    for p, _v in rows:
        o = _period_ord(p)
        if o is not None and o > best_ord:
            best, best_ord = p, o
    return best


def _yoy(conn, symbol, table, metric, period):
    cur = _value(conn, symbol, table, metric, period)
    prev_p = _shift_year(period)
    prev = _value(conn, symbol, table, metric, prev_p) if prev_p else None
    if cur is None or prev is None or prev == 0:
        return None, cur, prev, prev_p
    return round((cur - prev) / abs(prev) * 100, 1), cur, prev, prev_p


def _fmt_pct(v: float | None, digits=1) -> str:
    if v is None:
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.{digits}f}%"


def _fmt_band(low, high, suffix="%") -> str:
    if low is None and high is None:
        return ""
    if high is None:
        return f"at least {low:g}{suffix}"
    if low is None or abs(low - high) < 0.05:
        return f"{(high if low is None else low):g}{suffix}"
    return f"{low:g}–{high:g}{suffix}"


def _range_verdict(actual: float, low, high, tol: float, partly: float, above: str = "beat") -> str:
    """above='beat': clearing the top of a growth range counts as delivered.
    above='soft': a margin level above the band stays 'partly' — screener's
    operating margin is often a different line from the one management guides."""
    lo = low if low is not None else high
    hi = high if high is not None else low
    if lo is None or actual is None:
        return "Can't verify"
    if hi is None:
        if actual >= lo - tol:
            return "Delivered" if actual >= lo else "Partly"
        if actual >= lo - partly:
            return "Partly"
        return "Missed"
    if lo > hi:
        lo, hi = hi, lo
    if lo - tol <= actual <= hi + tol:
        return "Delivered"
    if actual > hi:
        return "Partly" if above == "soft" else "Delivered"
    dist = lo - actual
    if dist <= partly:
        return "Partly"
    return "Missed"


def _direction_verdict(actual: float | None, direction: str, flat_band: float) -> str:
    if actual is None or not direction:
        return "Can't verify"
    if direction == "flat":
        return "Delivered" if abs(actual) <= flat_band else ("Partly" if abs(actual) <= flat_band * 2 else "Missed")
    if direction == "up":
        if actual > flat_band:
            return "Delivered"
        if actual >= -flat_band:
            return "Partly"
        return "Missed"
    if actual < -flat_band:
        return "Delivered"
    if actual <= flat_band:
        return "Partly"
    return "Missed"


def _reconcile(metric: str, comparison: str, parsed: dict, quote: str) -> str:
    q = quote.lower()
    if parsed["unit"] == "pp":
        return "delta_pp"
    if parsed["unit"] == "direction":
        return "direction"
    if metric in ("revenue", "pat") and parsed["unit"] == "pct":
        return "growth"
    if metric == "operating_margin" and not re.search(r"grow|growth|expand|improve|bps|basis point", q):
        return "level"
    if metric == "operating_margin" and parsed["low"] is not None and parsed["low"] >= 8 and "bps" not in q:
        if not re.search(r"grow|growth|expand|improve", q):
            return "level"
    if comparison in ("growth", "level", "direction", "delta_pp"):
        return comparison
    return "growth"


def _fix_metric(metric: str, quote: str) -> str:
    q = quote.lower()
    # "10% to 11% growth" is not a margin level just because the model said so.
    if metric == "operating_margin" and re.search(r"\bgrowth\b", q) and not re.search(r"margin|ebitda|ebit", q):
        return "revenue"
    return metric


def resolve_item(conn, row: dict) -> dict:
    symbol = row["symbol"]
    quote = re.sub(r"\s*/\s*$", "", row["quote"] or "").strip()
    metric = _fix_metric(row["metric"], quote)
    parsed = targets_from_quote(quote) or {
        "low": row.get("low"), "high": row.get("high"), "unit": "pct",
        "interpreted": bool(row.get("interpreted")), "direction": None,
    }
    comparison = _reconcile(metric, row["comparison"], parsed, quote)
    horizon_kind, spec = resolve_horizon(row["horizon"], row["call_period"])
    low, high = parsed.get("low"), parsed.get("high")
    direction = parsed.get("direction") or direction_from_quote(quote)

    base = {
        "metric": metric,
        "metric_label": METRIC_LABEL.get(metric, metric),
        "comparison": comparison,
        "qualifier": row.get("qualifier") or "firm",
        "horizon": row.get("horizon") or "",
        "quote": quote,
        "speaker": row.get("speaker") or None,
        "chunk_id": row["chunk_id"],
        "page": row.get("page"),
        "call_period": row["call_period"],
        "interpreted": bool(parsed.get("interpreted")),
        "low": low,
        "high": high,
        "actual": None,
        "actual_label": None,
        "promise_label": "",
        "verdict": "Can't verify",
        "note": "",
        "bar": None,
        "excerpt": _excerpt(conn, symbol, row["chunk_id"], quote),
        "match_period": "",
    }

    if horizon_kind == "past":
        base["note"] = "This points at a period already reported on that call, so it is not a promise."
        base["verdict"] = "Can't verify"
        return base
    if metric not in CHECKABLE:
        base["promise_label"] = _promise_text(comparison, low, high, direction, metric)
        base["note"] = "No matching line in the standardised tables, so this cannot be checked."
        base["verdict"] = "Can't verify"
        return base
    if horizon_kind == "open":
        base["promise_label"] = _promise_text(comparison, low, high, direction, metric)
        base["note"] = "The horizon is not a single quarter or financial year, so it cannot be matched to one column."
        base["verdict"] = "Can't verify"
        return base
    if metric == "revenue" and re.search(r"\bpricing\b", quote, re.I) and not re.search(r"\brevenue\b", quote, re.I):
        base["promise_label"] = _promise_text(comparison, low, high, direction, metric)
        base["note"] = "This is a price increase, not total sales, so the sales column cannot check it."
        base["verdict"] = "Can't verify"
        return base
    if metric == "operating_margin" and re.search(r"\b(segment|telecom|jewellery|jewelry|division|vertical)\b", quote, re.I):
        base["promise_label"] = _promise_text(comparison, low, high, direction, metric)
        base["note"] = "This is a segment margin. The tables only have the company total, so it is not scored."
        base["verdict"] = "Can't verify"
        return base

    if horizon_kind == "quarter":
        q, fy = spec
        period = screener_quarter(q, fy)
        table = "quarterly"
        horizon_label = f"Q{q} FY{fy:02d} ({period})"
    else:
        period = screener_year(spec)
        table = "profit_loss"
        horizon_label = f"FY{spec:02d} ({period})"
    base["match_period"] = period

    level_metric = {"revenue": "Sales+", "operating_margin": "OPM %", "pat": "Net Profit+"}[metric]
    # Banks store revenue as Revenue+. Fall back if Sales+ is absent.
    if metric == "revenue" and _value(conn, symbol, table, "Sales+", period) is None:
        if _value(conn, symbol, table, "Revenue+", period) is not None or _latest_period(conn, symbol, table, "Revenue+"):
            level_metric = "Revenue+"

    latest = _latest_period(conn, symbol, table, level_metric)
    target_ord = _period_ord(period)
    latest_ord = _period_ord(latest) if latest else None
    if latest_ord is None or (target_ord is not None and target_ord > latest_ord):
        base["promise_label"] = _promise_text(comparison, low, high, direction, metric)
        base["note"] = f"{horizon_label} is not in the tables yet. Latest {table.replace('_', ' ')} column is {latest or 'unavailable'}."
        base["verdict"] = "Pending"
        return base

    growth_metric = level_metric if metric != "operating_margin" else "OPM %"
    growth, cur, prev, prev_p = _yoy(conn, symbol, table, growth_metric, period)
    # margin "growth" from _yoy on a percent is a percent-of-percent. For margin
    # movement we want percentage points, computed below.
    if metric == "operating_margin":
        cur_m = _value(conn, symbol, table, level_metric, period)
        prev_m = _value(conn, symbol, table, level_metric, _shift_year(period))
        delta = round(cur_m - prev_m, 1) if cur_m is not None and prev_m is not None else None
    else:
        cur_m = prev_m = delta = None

    if comparison == "level" and metric == "operating_margin":
        actual = cur_m
        if actual is None:
            base.update(_cant(period))
            base["promise_label"] = _promise_text(comparison, low, high, direction, metric)
            return base
        verdict = _range_verdict(actual, low, high, tol=0.5, partly=2.5, above="soft")
        base.update({
            "actual": actual,
            "actual_label": f"{actual:g}%",
            "promise_label": _fmt_band(low, high) + " margin",
            "verdict": verdict,
            "note": (f"Screener operating margin in {period} is {actual:g}%. "
                     "That line is not always the margin management guides."),
            "bar": _bar(low, high, actual),
        })
        return base

    if comparison == "delta_pp" and metric == "operating_margin":
        actual = delta
        if actual is None:
            base.update(_cant(period))
            base["promise_label"] = _promise_text(comparison, low, high, direction, metric)
            return base
        verdict = _range_verdict(actual, low, high, tol=0.2, partly=0.6)
        base.update({
            "actual": actual,
            "actual_label": f"{_fmt_pct(actual)} pts vs { _shift_year(period)}",
            "promise_label": _fmt_band(low, high, " pts"),
            "verdict": verdict,
            "note": f"Margin {cur_m:g}% in {period} vs {prev_m:g}% a year earlier.",
            "bar": _bar(low, high, actual),
        })
        return base

    if comparison == "direction":
        if metric == "operating_margin":
            actual = delta
            flat = 0.4
            detail = (f"Margin {cur_m:g}% in {period} vs {prev_m:g}% a year earlier."
                      if cur_m is not None and prev_m is not None else f"No margin pair for {period}.")
            label = f"{_fmt_pct(actual)} pts" if actual is not None else None
        else:
            actual = growth
            flat = 2.0
            if cur is None or prev is None:
                detail = f"Need both {period} and the year-earlier column to judge a direction."
                label = None
            else:
                unit = "cr"
                detail = f"{level_metric.replace('+', '')} {cur:,.0f} {unit} in {period} vs {prev:,.0f} in {prev_p}."
                label = _fmt_pct(actual) + " YoY"
        if actual is None:
            base["promise_label"] = {"up": "higher", "down": "lower", "flat": "about flat"}.get(direction or "", "a direction")
            base["note"] = detail
            base["verdict"] = "Can't verify"
            return base
        verdict = _direction_verdict(actual, direction or "up", flat)
        base.update({
            "actual": actual,
            "actual_label": label,
            "promise_label": {"up": "higher", "down": "lower", "flat": "about flat"}.get(direction or "", "a direction"),
            "verdict": verdict,
            "note": detail,
            "bar": None,
        })
        return base

    # growth (revenue or pat). A margin "growth" without bps is treated as pp too
    # when the quoted number is small (< 8); otherwise as a level, already handled.
    if metric == "operating_margin":
        actual = delta
        if actual is None:
            base.update(_cant(period))
            base["promise_label"] = _promise_text("delta_pp", low, high, direction, metric)
            return base
        verdict = _range_verdict(actual, low, high, tol=0.3, partly=1.0)
        base.update({
            "actual": actual,
            "actual_label": f"{_fmt_pct(actual)} pts",
            "promise_label": _fmt_band(low, high, " pts"),
            "verdict": verdict,
            "note": f"Margin {cur_m:g}% in {period} vs {prev_m:g}% a year earlier.",
            "bar": _bar(low, high, actual),
        })
        return base

    actual = growth
    if cur is None or prev is None or prev == 0:
        base["promise_label"] = _promise_text("growth", low, high, direction, metric)
        if cur is None:
            base["note"] = f"{horizon_label} has no {level_metric.replace('+','')} figure."
            base["verdict"] = "Can't verify"
        else:
            base["note"] = f"{period} is reported, but the year-earlier base is missing or zero, so YoY is not meaningful."
            base["verdict"] = "Can't verify"
        return base
    name = "Sales" if metric == "revenue" else "Net profit"
    if metric == "revenue" and re.search(r"constant currency", quote, re.I):
        base.update({
            "actual": actual,
            "actual_label": _fmt_pct(actual) + " YoY in rupees",
            "promise_label": _fmt_band(low, high) + " constant currency",
            "verdict": "Can't verify",
            "note": (f"Rupee sales were ₹{cur:,.0f} cr in {period} vs ₹{prev:,.0f} cr ({_fmt_pct(actual)}). "
                     "Constant-currency guidance is not in these tables, so it is not scored."),
            "bar": None,
        })
        return base
    verdict = _range_verdict(actual, low, high, tol=1.0, partly=3.0)
    base.update({
        "actual": actual,
        "actual_label": _fmt_pct(actual) + " YoY",
        "promise_label": _fmt_band(low, high) + " YoY",
        "verdict": verdict,
        "note": (f"{name} ₹{cur:,.0f} cr in {period} vs ₹{prev:,.0f} cr in {prev_p}."
                 + (" The quote says this continues onwards; the check is the first year of that stretch."
                    if re.search(r"onwards", quote, re.I) else "")),
        "bar": _bar(low, high, actual),
    })
    return base


def _cant(period):
    return {"verdict": "Can't verify", "note": f"The {period} column is missing a comparable figure."}


def _promise_text(comparison, low, high, direction, metric) -> str:
    if comparison == "direction" or (low is None and high is None):
        word = {"up": "higher", "down": "lower", "flat": "about flat"}.get(direction or "", "a direction")
        return word
    suffix = " pts" if comparison == "delta_pp" else (" margin" if metric == "operating_margin" and comparison == "level" else "% YoY" if comparison == "growth" else "%")
    # _fmt_band already has %
    if comparison == "delta_pp":
        return _fmt_band(low, high, " pts")
    if comparison == "level":
        return _fmt_band(low, high) + " margin"
    return _fmt_band(low, high) + " YoY"


def _bar(low, high, actual):
    if actual is None or (low is None and high is None):
        return None
    lo = low if low is not None else high
    hi = high if high is not None else low
    if lo is None:
        return None
    if hi is None:
        hi = lo
    lo, hi = (min(lo, hi), max(lo, hi))
    span = max(hi - lo, 1)
    mn = min(lo, actual, 0) - span * 0.35
    mx = max(hi, actual) + span * 0.35
    if mx <= mn:
        return None
    return {"low": lo, "high": hi, "actual": round(actual, 2), "min": round(mn, 2), "max": round(mx, 2)}


def _excerpt(conn, symbol, chunk_id, quote) -> str:
    row = conn.execute(
        "SELECT text FROM doc_chunks WHERE symbol=? AND chunk_id=?",
        (symbol, chunk_id),
    ).fetchone()
    if not row or not row[0]:
        return ""
    text = re.sub(r"\s+", " ", row[0]).strip()
    nq = _norm(quote)
    nt = _norm(text)
    idx = nt.find(nq[:40]) if nq else -1
    if idx < 0:
        return text[:420]
    # map roughly back: norm index is close enough to a window on the original
    start = max(0, int(idx * len(text) / max(len(nt), 1)) - 80)
    return text[start:start + 460].strip()


def _call_sort_key(period: str):
    parsed = parse_qfy(period or "")
    if not parsed:
        return (0, 0)
    q, fy = parsed
    return (fy, q)


def pattern_and_meter(items: list[dict]) -> dict:
    counts = {"Delivered": 0, "Partly": 0, "Missed": 0, "Pending": 0, "Can't verify": 0}
    for it in items:
        counts[it["verdict"]] = counts.get(it["verdict"], 0) + 1
    resolved = [i for i in items if i["verdict"] in ("Delivered", "Partly", "Missed")]
    n = len(resolved)
    hit = None if n == 0 else round((counts["Delivered"] + 0.5 * counts["Partly"]) / n, 2)
    if n == 0 and counts["Pending"]:
        text = (f"{counts['Pending']} explicit promise{'s' if counts['Pending'] != 1 else ''} "
                f"{'are' if counts['Pending'] != 1 else 'is'} still open. "
                "The period named has not been reported in the tables yet.")
    elif n == 0:
        text = "Nothing in the last four calls could be tied to a reported revenue, margin, or profit figure."
    else:
        text = (f"Of {n} resolved promise{'s' if n != 1 else ''}, "
                f"{counts['Delivered']} landed, {counts['Partly']} {'were' if counts['Partly'] != 1 else 'was'} close, "
                f"{counts['Missed']} missed.")
        def _top(band):
            return band if band is not None else 0
        beats = [i for i in resolved if i["verdict"] == "Delivered" and i.get("actual") is not None and i.get("high") is not None and i["actual"] > i["high"]]
        misses = [i for i in resolved if i["verdict"] == "Missed" and i.get("actual") is not None and i.get("low") is not None]
        if beats:
            beat = max(beats, key=lambda i: i["actual"] - _top(i.get("high")))
            text += f" Biggest beat: {beat['metric_label']} {beat['actual_label']} against {beat['promise_label']}."
        if misses:
            miss = min(misses, key=lambda i: i["actual"] - (i["low"] or 0))
            text += f" Biggest miss: {miss['metric_label']} {miss['actual_label']} against {miss['promise_label']}."
        if counts["Pending"]:
            text += f" {counts['Pending']} more still pending."
    return {"hit_rate": hit, "counts": counts, "pattern": text, "resolved": n}


def _width(item: dict) -> float:
    lo, hi = item.get("low"), item.get("high")
    if lo is None:
        return 99
    span = 20 if hi is None else abs(hi - lo)
    if item.get("interpreted"):
        span += 5
    return span


def _dedupe(items: list[dict]) -> list[dict]:
    """Same call, same metric, same matched period: keep the tighter quote."""
    best: dict[tuple, dict] = {}
    order: list[dict] = []
    for it in items:
        if not it.get("match_period"):
            order.append(it)
            continue
        key = (it["call_period"], it["metric"], it["match_period"], it["comparison"])
        prev = best.get(key)
        if prev is None:
            best[key] = it
            order.append(it)
        elif _width(it) < _width(prev):
            order[order.index(prev)] = it
            best[key] = it
    return order


def promised_for(symbol: str) -> dict:
    symbol = (symbol or "").upper()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ensure_table(conn)
        calls = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM doc_chunks WHERE symbol=? AND doc_kind='CONCALL'",
            (symbol,),
        ).fetchall()]
        calls = [c for c in calls if parse_qfy(c or "")]
        calls.sort(key=_call_sort_key)
        focus = calls[-4:]
        try:
            n_runs = conn.execute(
                "SELECT COUNT(*) FROM guidance_runs WHERE symbol=?", (symbol,)
            ).fetchone()[0]
        except sqlite3.OperationalError:
            n_runs = 0
        if not focus:
            return {
                "symbol": symbol, "status": "no_corpus", "focus": [],
                "groups": [], "hit_rate": None, "counts": {}, "pattern": "",
                "note": "No concall transcripts in the snapshot for this ticker.",
            }
        rows = conn.execute(
            "SELECT * FROM guidance_items WHERE symbol=? AND call_period IN ({})".format(
                ",".join("?" * len(focus))
            ),
            (symbol, *focus),
        ).fetchall()
        if not rows and n_runs == 0:
            return {
                "symbol": symbol, "status": "not_extracted", "focus": focus,
                "groups": [], "hit_rate": None, "counts": {}, "pattern": "",
                "note": "Transcripts are loaded. Guidance has not been read from them yet.",
            }
        items = _dedupe([resolve_item(conn, dict(r)) for r in rows])
        for it in items:
            if re.search(r"constant currency", it.get("quote") or "", re.I) and "onstant-currency" not in (it.get("note") or ""):
                it["note"] = (it.get("note") or "").rstrip() + " The quote is constant-currency; rupee sales are a different measure."
        # Drop same-quarter restatements from the score, but keep them out of the UI too.
        items = [i for i in items if "already reported" not in (i.get("note") or "")]
        summary = pattern_and_meter(items)
        groups = []
        for period in reversed(focus):
            group_items = [i for i in items if i["call_period"] == period]
            if not group_items:
                continue
            groups.append({"call_period": period, "label": _pretty_call(period), "items": group_items})
        status = "ready" if items else "empty"
        return {
            "symbol": symbol,
            "status": status,
            "focus": focus,
            "groups": groups,
            "hit_rate": summary["hit_rate"],
            "counts": summary["counts"],
            "pattern": summary["pattern"],
            "note": ("Quotes are copied from concall transcripts. "
                     "Delivered figures are computed from screener's standardised tables, which are derived from filings."),
        }
    finally:
        conn.close()


def _pretty_call(period: str) -> str:
    m = re.match(r"Q([1-4])FY(\d{2})", period or "")
    if not m:
        return period
    return f"Q{m.group(1)} FY{m.group(2)} call"


def corpus_symbols() -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM doc_chunks WHERE doc_kind='CONCALL' ORDER BY symbol"
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
