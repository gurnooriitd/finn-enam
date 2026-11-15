"""
Push — Results summary (deterministic, from screener standardised quarterly data).
No LLM, no invented numbers: anything missing becomes None + an honest note.

The filing's quarter is matched from the filing text ("...ended 30th September
2026"); fallback: latest screener quarter-end on or before the filing date.
"""
import re
import sqlite3
import datetime

from backend.config import RAW_DIR

Q_MONTHS = {3: "Mar", 6: "Jun", 9: "Sep", 12: "Dec"}
MONTH_NUM = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

# Screener metric names -> display names
REVENUE_METRICS = ("Sales+", "Revenue+")
PBT = "Profit before tax"
PAT = "Net Profit+"
EBITDA = "Operating Profit"


def _quarter_end_for(y: int, m: int, d: int) -> tuple[int, int] | None:
    """Latest quarter end (Mar/Jun/Sep/Dec) on or before the given date."""
    for qm, last in ((12, 31), (9, 30), (6, 30), (3, 31)):
        if (m, d) >= (qm, last) or m > qm:
            return (y, qm)
    return (y - 1, 12)


def _label(y: int, m: int) -> str:
    return f"{Q_MONTHS[m]} {y}"


def _parse_quarter_from_text(text: str) -> tuple[int, int] | None:
    """Pull a period-end date out of filing text. Returns (year, month)."""
    if not text:
        return None
    t = text[:1200]
    MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    pats = (
        rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({MON})\s*[,']?\s*(\d{{4}})",   # 30th September 2026
        rf"({MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*[,']?\s*(\d{{4}})",   # September 30, 2026
        r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})",                          # 30.09.2026
    )
    for i, pat in enumerate(pats):
        m = re.search(pat, t, re.I)
        if not m:
            continue
        g = m.groups()
        try:
            if i == 0:
                d, mon, y = int(g[0]), g[1], int(g[2])
            elif i == 1:
                mon, d, y = g[0], int(g[1]), int(g[2])
            else:
                d, mon, y = int(g[0]), int(g[1]), int(g[2])
        except ValueError:
            continue
        mon_n = MONTH_NUM.get(mon.lower()[:3]) if isinstance(mon, str) else mon
        if mon_n and 2015 <= y <= 2040 and 1 <= d <= 31:
            return _quarter_end_for(y, mon_n, d)
    return None


def _load_quarterly(symbol: str, conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """{period: {metric: value}} for the quarterly table."""
    out: dict[str, dict[str, float]] = {}
    for period, metric, value in conn.execute(
        "SELECT period, metric, value FROM screener_fundamentals "
        "WHERE symbol=? AND table_type='quarterly'",
        (symbol,),
    ):
        if value is not None:
            out.setdefault(period, {})[metric] = value
    return out


def _pct(cur: float | None, old: float | None) -> float | None:
    if cur is None or old is None or old == 0:
        return None
    return round((cur - old) / abs(old) * 100, 1)


def _shift(label: str, months: int) -> str | None:
    m = re.match(r"([A-Za-z]{3}) (\d{4})", label)
    if not m:
        return None
    mon = MONTH_NUM.get(m.group(1).lower())
    if not mon:
        return None
    y = int(m.group(2))
    total = y * 12 + (mon - 1) - months
    ny, nm = divmod(total, 12)
    nm += 1
    return _label(ny, nm) if nm in Q_MONTHS else None


def _basis(symbol: str) -> str:
    for variant, name in (("consolidated", "consolidated"), ("standalone", "standalone")):
        if (RAW_DIR / "screener" / f"{symbol}_{variant}.html").exists():
            return name
    return "consolidated"


def results_summary(symbol: str, ts_iso: str, texts: list[str], conn: sqlite3.Connection) -> dict | None:
    """Build the 4-row results table for a results filing, or None if no data."""
    quarters = _load_quarterly(symbol, conn)
    if not quarters:
        return None

    # Which quarter does the filing cover?
    filing_q = None
    for t in texts:
        filing_q = _parse_quarter_from_text(t or "")
        if filing_q:
            break
    if filing_q is None:
        d = (ts_iso or "")[:10]
        try:
            filing_q = _quarter_end_for(*[int(x) for x in d.split("-")])
        except (ValueError, TypeError):
            filing_q = None

    filing_label = _label(*filing_q) if filing_q else None
    shown = filing_label if filing_label in quarters else None
    note = ""
    if shown is None:
        # Not yet in standardised data (or unparseable): show the latest we have.
        shown = max(quarters, key=lambda p: (p.split()[1], Q_MONTHS_INV[p.split()[0]]))
        if filing_label:
            note = f"filing quarter {filing_label} not yet in standardised data"
        else:
            note = "filing quarter unclear; showing latest standardised quarter"

    q = quarters[shown]
    revenue = next((q[k] for k in REVENUE_METRICS if k in q), None)
    ebitda = q.get(EBITDA)
    pbt = q.get(PBT)
    pat = q.get(PAT)

    is_bank = ebitda is None and ("Financing Profit" in q or "Revenue+" in q)
    if is_bank:
        ebitda = None

    rows = []
    for disp, val in (("Revenue", revenue), ("EBITDA", ebitda), ("PBT", pbt), ("PAT", pat)):
        if val is None and disp == "EBITDA" and not is_bank:
            continue
        prev, yoy = _shift(shown, 3), _shift(shown, 12)
        pq = quarters.get(prev or "", {})
        yq = quarters.get(yoy or "", {})
        prev_rev = next((pq[k] for k in REVENUE_METRICS if k in pq), None)
        yoy_rev = next((yq[k] for k in REVENUE_METRICS if k in yq), None)
        if disp == "Revenue":
            qoq, yoy_pct = _pct(val, prev_rev), _pct(val, yoy_rev)
            qoq_abs = (round(val - prev_rev, 1) if val is not None and prev_rev is not None else None)
            yoy_abs = (round(val - yoy_rev, 1) if val is not None and yoy_rev is not None else None)
        else:
            p_base = pq.get(EBITDA if disp == "EBITDA" else PBT if disp == "PBT" else PAT)
            y_base = yq.get(EBITDA if disp == "EBITDA" else PBT if disp == "PBT" else PAT)
            qoq, yoy_pct = _pct(val, p_base), _pct(val, y_base)
            qoq_abs = (round(val - p_base, 1) if val is not None and p_base is not None else None)
            yoy_abs = (round(val - y_base, 1) if val is not None and y_base is not None else None)
        rows.append({
            "metric": disp, "value": val,
            "qoq_pct": qoq, "yoy_pct": yoy_pct,
            "qoq_abs": qoq_abs, "yoy_abs": yoy_abs,
        })

    opm = q.get("OPM %")
    ebitda_margin = round(opm, 1) if opm is not None else (
        _pct_margin(ebitda, revenue))
    pat_margin = _pct_margin(pat, revenue)

    if is_bank:
        note = (note + "; " if note else "") + "EBITDA n/a for banks/NBFCs"

    return {
        "quarter": shown,
        "filing_quarter": filing_label,
        "basis": _basis(symbol),
        "unit": "Rs crore",
        "rows": rows,
        "ebitda_margin": ebitda_margin,
        "pat_margin": pat_margin,
        "note": note,
        "available": True,
    }


def _pct_margin(part: float | None, whole: float | None) -> float | None:
    if part is None or not whole:
        return None
    return round(part / whole * 100, 1)


Q_MONTHS_INV = {v: k for k, v in Q_MONTHS.items()}
