"""
Push — Context helpers
Timestamp parsing and per-filing price context (day % and volume ratio
computed at the filing's date, not the latest date).

Rules:
- NSE ts format: "01-Nov-2026 09:43:51" (IST, exchange time).
- If the filing lands before market close (15:30 IST) AND its date is a
  trading day, use that day's session.
- Otherwise (after close, weekend, holiday) use the NEXT trading day's move.
- No price data for the resolved day -> all None (UI shows nothing).
"""
import datetime
import html
import re

from backend.config import RAW_DIR

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
MARKET_CLOSE = datetime.time(15, 30)


def parse_ts(ts: str) -> datetime.datetime | None:
    """Parse NSE announcement ts to a naive datetime (IST)."""
    if not ts:
        return None
    ts = ts.strip()
    # "01-Nov-2026 09:43:51" or "01-Nov-2026"
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.datetime.strptime(ts, fmt)
        except ValueError:
            continue
    # Fallback: ISO-like
    try:
        return datetime.datetime.fromisoformat(ts)
    except ValueError:
        return None


def price_context(symbol: str, ts: str, conn) -> dict:
    """Day % and 20-session volume ratio at the filing's date.

    Returns {day_pct, vol_ratio, price_date} — Nones when unknowable.
    """
    out = {"day_pct": None, "vol_ratio": None, "price_date": None}
    dt = parse_ts(ts)
    if dt is None:
        return out
    filing_date = dt.date()

    def _session(d: datetime.date):
        row = conn.execute(
            "SELECT close, volume FROM prices WHERE symbol=? AND date=?",
            (symbol, d.isoformat()),
        ).fetchone()
        return row

    session = None
    if dt.time() < MARKET_CLOSE:
        session = _session(filing_date)
        if session is not None:
            out["price_date"] = filing_date.isoformat()

    if session is None:
        row = conn.execute(
            """SELECT date, close, volume FROM prices
               WHERE symbol=? AND date>? ORDER BY date LIMIT 1""",
            (symbol, filing_date.isoformat()),
        ).fetchone()
        if row is None:
            return out
        out["price_date"] = row["date"]
        session = row

    prev = conn.execute(
        "SELECT close FROM prices WHERE symbol=? AND date<? ORDER BY date DESC LIMIT 1",
        (symbol, out["price_date"]),
    ).fetchone()
    if prev and prev["close"]:
        out["day_pct"] = round((session["close"] - prev["close"]) / prev["close"] * 100, 2)

    prior = conn.execute(
        """SELECT volume FROM prices WHERE symbol=? AND date<?
           ORDER BY date DESC LIMIT 20""",
        (symbol, out["price_date"]),
    ).fetchall()
    if prior:
        avg = sum(r["volume"] for r in prior) / len(prior)
        if avg > 0:
            out["vol_ratio"] = round(session["volume"] / avg, 2)
    return out


_NAME_CACHE: dict[str, str] = {}


def company_name(symbol: str) -> str:
    """Real company name from the cached screener page <title>; symbol if unknown."""
    if symbol in _NAME_CACHE:
        return _NAME_CACHE[symbol]
    name = symbol
    for variant in ("consolidated", "standalone"):
        p = RAW_DIR / "screener" / f"{symbol}_{variant}.html"
        if p.exists():
            m = re.search(r"<title>([^<\n|-]+)", p.read_text(errors="ignore"))
            if m:
                name = html.unescape(re.sub(r"\s+share price\s*$", "", m.group(1).strip()))
                break
    _NAME_CACHE[symbol] = name
    return name
