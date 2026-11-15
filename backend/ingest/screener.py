"""
Screener.in ingest — quarterly, P&L, balance sheet, cash flow, ratios + docs list.
No PDF downloads here (Phase 3).
Caches raw HTML to data/raw/screener/.
Polite: 1.5s between requests.
"""
import json
import re
import sqlite3
import time
import datetime
import hashlib
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from backend.config import PORTFOLIO_TICKERS, DB_PATH, RAW_DIR, NSE_SLEEP

SCREENER_RAW = RAW_DIR / "screener"
SCREENER_RAW.mkdir(parents=True, exist_ok=True)

SCREENER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FINN-research-bot/1.0; +https://github.com/finn)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def _cache_path(symbol: str, variant: str) -> Path:
    return SCREENER_RAW / f"{symbol}_{variant}.html"


def _fetch_page(symbol: str) -> BeautifulSoup | None:
    for variant in ("consolidated", ""):
        cp = _cache_path(symbol, variant or "standalone")
        if cp.exists():
            age = datetime.datetime.now().timestamp() - cp.stat().st_mtime
            if age < 12 * 3600:
                return BeautifulSoup(cp.read_text(), "html.parser"), variant

    for variant in ("consolidated", ""):
        url = f"https://www.screener.in/company/{symbol}/" + (f"{variant}/" if variant else "")
        try:
            r = requests.get(url, headers=SCREENER_HEADERS, timeout=20)
            if r.status_code == 200:
                cp = _cache_path(symbol, variant or "standalone")
                cp.write_text(r.text)
                time.sleep(NSE_SLEEP)
                return BeautifulSoup(r.text, "html.parser"), variant
        except Exception as e:
            print(f"  screener {symbol} {variant}: {e}")
    return None, None


def _parse_number(s: str) -> float | None:
    s = re.sub(r"[,%\s]", "", str(s)).strip()
    try:
        return float(s)
    except ValueError:
        return None


def _parse_table(soup: BeautifulSoup, section_id: str) -> list[tuple[str, str, float | None]]:
    """Parse a screener table section. Returns (period, metric, value) tuples."""
    section = soup.find("section", {"id": section_id})
    if not section:
        return []

    table = section.find("table")
    if not table:
        return []

    rows = table.find_all("tr")
    if not rows:
        return []

    # Header row has period labels
    header = rows[0]
    periods = [th.get_text(strip=True) for th in header.find_all(["th", "td"])[1:]]

    result = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        metric = cells[0].get_text(strip=True)
        for i, cell in enumerate(cells[1:]):
            if i < len(periods):
                val = _parse_number(cell.get_text(strip=True))
                result.append((periods[i], metric, val))
    return result


def _parse_docs(soup: BeautifulSoup, symbol: str) -> list[dict]:
    docs = []
    # Look for annual reports and concall sections
    for section in soup.find_all("section"):
        heading = section.find(["h2", "h3"])
        if not heading:
            continue
        head_text = heading.get_text(strip=True).lower()
        if "annual report" in head_text or "concall" in head_text or "transcript" in head_text or "document" in head_text:
            for a in section.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)
                if href.endswith(".pdf") or "pdf" in href.lower() or "corpfiling" in href or "AnnPdf" in href:
                    kind = "annual_report" if "annual" in text.lower() or "ar" in text.lower() else "concall"
                    docs.append({"symbol": symbol, "kind": kind, "period": text, "url": href})

    # Also check the documents list
    doc_section = soup.find(id="documents") or soup.find("section", {"id": re.compile(r"document", re.I)})
    if doc_section:
        for li in doc_section.find_all("li"):
            a = li.find("a", href=True)
            if a and (".pdf" in a["href"].lower() or "corpfiling" in a["href"]):
                text = a.get_text(strip=True)
                kind = "annual_report" if "annual" in text.lower() else "concall"
                docs.append({"symbol": symbol, "kind": kind, "period": text, "url": a["href"]})

    return docs


def upsert_fundamentals(symbol: str, table_type: str, rows: list[tuple], conn: sqlite3.Connection):
    cur = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()
    for period, metric, value in rows:
        cur.execute(
            """INSERT OR REPLACE INTO screener_fundamentals
               (symbol, table_type, period, metric, value, fetched_at)
               VALUES (?,?,?,?,?,?)""",
            (symbol, table_type, period, metric, value, now)
        )
    conn.commit()


def upsert_docs(docs: list[dict], conn: sqlite3.Connection):
    cur = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()
    for d in docs:
        cur.execute(
            """INSERT OR IGNORE INTO docs(symbol, kind, period, url, fetched_at)
               VALUES (?,?,?,?,?)""",
            (d["symbol"], d["kind"], d["period"], d["url"], now)
        )
    conn.commit()


TABLE_SECTIONS = {
    "quarterly": "quarters",
    "profit_loss": "profit-loss",
    "balance_sheet": "balance-sheet",
    "cash_flow": "cash-flow",
    "ratios": "ratios",
}


def run(tickers: list[str] | None = None):
    tickers = tickers or PORTFOLIO_TICKERS
    conn = sqlite3.connect(DB_PATH)
    fund_total = 0
    doc_total = 0

    for symbol in tickers:
        result = _fetch_page(symbol)
        if result[0] is None:
            print(f"  screener {symbol}: FAILED")
            continue
        soup, variant = result

        for table_type, section_id in TABLE_SECTIONS.items():
            rows = _parse_table(soup, section_id)
            if rows:
                upsert_fundamentals(symbol, table_type, rows, conn)
                fund_total += len(rows)

        docs = _parse_docs(soup, symbol)
        upsert_docs(docs, conn)
        doc_total += len(docs)
        print(f"  screener {symbol} ({variant}): fund rows={sum(len(_parse_table(soup,s)) for s in TABLE_SECTIONS.values())}, docs={len(docs)}")

    conn.close()
    print(f"screener total: {fund_total} fundamental rows, {doc_total} docs")
    return fund_total, doc_total


if __name__ == "__main__":
    run()
