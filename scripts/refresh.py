#!/usr/bin/env python3
"""
scripts/refresh.py — idempotent full refresh.
Run locally to rebuild the data snapshot.
Second run costs ~0 network (all caches hit).

Usage:
    cd finn/
    python scripts/refresh.py [--tickers RELIANCE TCS] [--skip-prices] [--skip-nse] [--skip-screener]
"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import init_db
from backend.config import PORTFOLIO_TICKERS


def main():
    parser = argparse.ArgumentParser(description="FINN data refresh")
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--skip-prices", action="store_true")
    parser.add_argument("--skip-nse", action="store_true")
    parser.add_argument("--skip-screener", action="store_true")
    parser.add_argument("--skip-classify", action="store_true")
    parser.add_argument("--skip-score", action="store_true")
    parser.add_argument("--skip-alerts", action="store_true")
    args = parser.parse_args()

    tickers = args.tickers or PORTFOLIO_TICKERS

    print("=== FINN Refresh ===")
    print(f"Tickers: {tickers}")

    # 1. Init DB
    print("\n[1/6] Init DB")
    init_db()

    # 2. Prices
    if not args.skip_prices:
        print("\n[2/6] Prices (yfinance, 120d)")
        from backend.ingest.prices import run as prices_run
        prices_run(tickers)
    else:
        print("\n[2/6] Prices: SKIPPED")

    # 3. NSE announcements + financial results
    if not args.skip_nse:
        print("\n[3/6] NSE announcements + financial results (30d)")
        from backend.ingest.nse import run as nse_run
        nse_run(tickers, days=30)
    else:
        print("\n[3/6] NSE: SKIPPED")

    # 4. Screener fundamentals + docs
    if not args.skip_screener:
        print("\n[4/6] Screener fundamentals + docs")
        from backend.ingest.screener import run as screener_run
        screener_run(tickers)
    else:
        print("\n[4/6] Screener: SKIPPED")

    # 5. Classify + score announcements
    if not args.skip_classify:
        print("\n[5/6] Classify announcements")
        from backend.push.classify import run_classify_all
        run_classify_all()

    if not args.skip_score:
        print("       Score announcements")
        from backend.push.priority import run_score_all
        run_score_all()

    # 6. Alerts scan
    if not args.skip_alerts:
        print("\n[6/6] Alert scan")
        from backend.push.alerts_scan import scan_alerts
        scan_alerts()

    # Row counts
    import sqlite3
    from backend.config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    tables = ["prices", "announcements", "nse_financial_results",
              "screener_fundamentals", "docs", "alert_events", "llm_cache"]
    print("\n=== Row counts ===")
    for t in tables:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {n}")
        except Exception:
            print(f"  {t}: ERROR")
    conn.close()

    print("\nRefresh complete.")


if __name__ == "__main__":
    main()
