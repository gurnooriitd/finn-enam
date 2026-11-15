"""
NSE ingest — announcements (30-day backfill) + financial results.
Uses plain requests with browser-like headers + session.
Caches raw JSON to data/raw/nse/.
Polite: 1.5s sleep between calls.
"""
import hashlib
import json
import sqlite3
import time
import datetime
from pathlib import Path

import requests

from backend.config import PORTFOLIO_TICKERS, DB_PATH, RAW_DIR, NSE_SLEEP

NSE_RAW = RAW_DIR / "nse"
NSE_RAW.mkdir(parents=True, exist_ok=True)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Origin": "https://www.nseindia.com",
}

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        try:
            s.get("https://www.nseindia.com/", timeout=10)
            time.sleep(1)
        except Exception:
            pass
        _session = s
    return _session


def _cache_path(name: str) -> Path:
    return NSE_RAW / f"{name}.json"


def _load_cache(name: str):
    p = _cache_path(name)
    if p.exists():
        age = datetime.datetime.now().timestamp() - p.stat().st_mtime
        if age < 6 * 3600:
            return json.loads(p.read_text())
    return None


def _save_cache(name: str, data):
    _cache_path(name).write_text(json.dumps(data))


def fetch_announcements(symbol: str, days: int = 30) -> list[dict]:
    s = _get_session()
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    # Date-ranged calls: key includes the range, so a new range never hits an
    # old cache. Only same-day re-runs reuse the fetch (6h TTL).
    cache_name = f"ann_{symbol}_{start.isoformat()}_{end.isoformat()}"
    cached = _load_cache(cache_name)
    if cached is not None:
        return cached

    url = (
        f"https://www.nseindia.com/api/corporate-announcements"
        f"?index=equities&symbol={symbol}"
        f"&from_date={start.strftime('%d-%m-%Y')}&to_date={end.strftime('%d-%m-%Y')}"
    )
    try:
        r = s.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        _save_cache(cache_name, data)
        time.sleep(NSE_SLEEP)
        return data
    except Exception as e:
        print(f"  NSE ann {symbol}: {e}")
        return []


def fetch_financial_results(symbol: str) -> list[dict]:
    cache_name = f"results_{symbol}"
    cached = _load_cache(cache_name)
    if cached is not None:
        return cached

    s = _get_session()
    url = (
        f"https://www.nseindia.com/api/corporates-financial-results"
        f"?index=equities&symbol={symbol}&period=Quarterly"
    )
    try:
        r = s.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            data = data.get("data", data.get("results", [data]))
        _save_cache(cache_name, data)
        time.sleep(NSE_SLEEP)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  NSE results {symbol}: {e}")
        return []


def _ann_id(symbol: str, item: dict) -> str:
    seq = item.get("seq_id") or item.get("seqNumber", "")
    return f"NSE-{symbol}-{seq}" if seq else hashlib.md5(
        f"{symbol}{item.get('an_dt','')}{item.get('desc','')[:40]}".encode()
    ).hexdigest()


def upsert_announcements(symbol: str, items: list[dict], conn: sqlite3.Connection):
    cur = conn.cursor()
    for item in items:
        ann_id = _ann_id(symbol, item)
        seq_id = str(item.get("seq_id") or item.get("seqNumber", ""))
        ts = item.get("an_dt") or item.get("exchdisstime") or item.get("broadCastDate", "")
        subject = item.get("desc") or item.get("subject", "")
        raw_desc = item.get("attchmntText") or item.get("difference", "")
        voice_summary = item.get("voiceSummary", "")
        att = item.get("attchmntFile") or ""
        if att and not att.startswith("http"):
            att = f"https://nsearchives.nseindia.com/corporate/{att}"

        from backend.push.context import parse_ts
        parsed = parse_ts(ts)
        ts_iso = parsed.isoformat(sep=" ") if parsed else None

        cur.execute(
            """INSERT OR IGNORE INTO announcements
               (id, symbol, ts, ts_iso, subject, raw_desc, voice_summary, attachment_url, exchange, seq_id)
               VALUES (?,?,?,?,?,?,?,?,'NSE',?)""",
            (ann_id, symbol, ts, ts_iso, subject, raw_desc, voice_summary, att, seq_id)
        )
    conn.commit()


def upsert_financial_results(symbol: str, items: list[dict], conn: sqlite3.Connection):
    cur = conn.cursor()
    for item in items:
        seq = str(item.get("seqNumber", item.get("seq_id", "")))
        period = item.get("period", "")
        consolidated = 1 if str(item.get("consolidated", "")).lower() in ("consolidated", "y", "yes", "1") else 0
        audited = 1 if str(item.get("audited", "")).lower() in ("audited", "un-audited".lower()) else 0
        # Note: "Un-Audited" means NOT audited; actual audited = when field says "Audited"
        audited = 1 if "Audited" == item.get("audited", "") else 0
        item_id = f"NSE-FIN-{symbol}-{seq}" if seq else hashlib.md5(
            f"{symbol}{period}{consolidated}".encode()).hexdigest()

        cur.execute(
            """INSERT OR IGNORE INTO nse_financial_results
               (id, symbol, period, from_date, to_date, filing_date,
                consolidated, audited, xbrl, result_link, result_description, financial_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item_id, symbol, period,
                item.get("fromDate", ""), item.get("toDate", ""),
                item.get("filingDate", item.get("broadCastDate", "")),
                consolidated, audited,
                item.get("xbrl", ""), item.get("resultDetailedDataLink", ""),
                item.get("resultDescription", ""), item.get("financialYear", ""),
            )
        )
    conn.commit()


def run(tickers: list[str] | None = None, days: int = 30):
    tickers = tickers or PORTFOLIO_TICKERS
    conn = sqlite3.connect(DB_PATH)
    ann_total = 0
    res_total = 0
    for t in tickers:
        anns = fetch_announcements(t, days)
        upsert_announcements(t, anns, conn)
        ann_total += len(anns)
        print(f"  NSE ann {t}: {len(anns)}")

        results = fetch_financial_results(t)
        upsert_financial_results(t, results, conn)
        res_total += len(results)
        print(f"  NSE results {t}: {len(results)}")

    conn.close()
    print(f"NSE total: {ann_total} announcements, {res_total} financial result rows")
    return ann_total, res_total


if __name__ == "__main__":
    run()
