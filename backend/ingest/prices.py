"""
Prices ingest — yfinance .NS, 120 trading days, all 18 portfolio tickers.
Writes to prices table. Idempotent (upsert). Caches raw CSV to data/raw/prices/.
"""
import csv
import json
import sqlite3
import datetime
from pathlib import Path

import yfinance as yf

from backend.config import PORTFOLIO_TICKERS, DB_PATH, RAW_DIR


PRICES_RAW = RAW_DIR / "prices"
PRICES_RAW.mkdir(parents=True, exist_ok=True)


def fetch_prices(ticker: str, days: int = 120) -> list[dict]:
    cache_file = PRICES_RAW / f"{ticker}.csv"
    # Cache invalidation: stale if older than 6h on a weekday
    if cache_file.exists():
        age = datetime.datetime.now().timestamp() - cache_file.stat().st_mtime
        if age < 6 * 3600:
            rows = []
            with open(cache_file) as f:
                for r in csv.DictReader(f):
                    rows.append(r)
            return rows

    sym = f"{ticker}.NS"
    df = yf.Ticker(sym).history(period=f"{days}d", auto_adjust=True)
    if df.empty:
        # fallback .BO
        df = yf.Ticker(f"{ticker}.BO").history(period=f"{days}d", auto_adjust=True)

    if df.empty:
        return []

    rows = []
    for date, row in df.iterrows():
        rows.append({
            "symbol": ticker,
            "date": str(date.date()),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "adj_close": float(row["Close"]),  # auto_adjust=True means Close IS adj
            "volume": int(row["Volume"]),
        })

    # Cache
    if rows:
        with open(cache_file, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)

    return rows


def upsert_prices(rows: list[dict], conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.executemany(
        """INSERT OR REPLACE INTO prices(symbol,date,open,high,low,close,adj_close,volume)
           VALUES(:symbol,:date,:open,:high,:low,:close,:adj_close,:volume)""",
        rows,
    )
    conn.commit()


def run(tickers: list[str] | None = None, days: int = 120):
    tickers = tickers or PORTFOLIO_TICKERS
    conn = sqlite3.connect(DB_PATH)
    total = 0
    for t in tickers:
        rows = fetch_prices(t, days)
        if rows:
            upsert_prices(rows, conn)
            total += len(rows)
            print(f"  prices {t}: {len(rows)} rows")
        else:
            print(f"  prices {t}: NO DATA")
    conn.close()
    print(f"prices total: {total} rows")
    return total


if __name__ == "__main__":
    run()
