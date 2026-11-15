"""
Spike: yfinance price data for 3 NSE tickers.
Tests: .NS suffix, 120d OHLCV, 20d volume average, adjusted close.
Saves raw output to data/spikes/.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
from pathlib import Path

SPIKES_DIR = Path(__file__).parent.parent / "data" / "spikes"
SPIKES_DIR.mkdir(parents=True, exist_ok=True)

TICKERS = ["RELIANCE.NS", "TCS.NS", "ETERNAL.NS"]  # ETERNAL = ex-Zomato

results = {}

for ticker_str in TICKERS:
    sym = ticker_str.replace(".NS", "")
    print(f"\n{'='*50}")
    print(f"Fetching {ticker_str} ...")
    try:
        t = yf.Ticker(ticker_str)
        hist = t.history(period="6mo", auto_adjust=True)

        if hist.empty:
            # Try .BO fallback
            print(f"  .NS empty, trying .BO ...")
            t2 = yf.Ticker(sym + ".BO")
            hist = t2.history(period="6mo", auto_adjust=True)
            if hist.empty:
                results[sym] = {"status": "BLOCKED", "error": "empty on both .NS and .BO"}
                continue
            ticker_str = sym + ".BO"

        # Last 120 trading days (approx 6mo)
        hist = hist.tail(120)

        # Compute 20d volume average (excluding today)
        hist["vol_20d_avg"] = hist["Volume"].shift(1).rolling(20).mean()
        hist["vol_ratio"] = hist["Volume"] / hist["vol_20d_avg"]

        last = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else last

        day_pct = (last["Close"] - prev["Close"]) / prev["Close"] * 100

        summary = {
            "status": "OK",
            "ticker_used": ticker_str,
            "rows": len(hist),
            "date_range": f"{hist.index[0].date()} → {hist.index[-1].date()}",
            "last_close": round(float(last["Close"]), 2),
            "day_pct": round(day_pct, 2),
            "last_volume": int(last["Volume"]),
            "vol_20d_avg": round(float(last["vol_20d_avg"]), 0) if pd.notna(last["vol_20d_avg"]) else None,
            "vol_ratio": round(float(last["vol_ratio"]), 2) if pd.notna(last["vol_ratio"]) else None,
            "columns": list(hist.columns),
        }
        results[sym] = summary
        print(f"  ✓ {ticker_str}: {summary['rows']} rows, last={summary['last_close']}, day%={summary['day_pct']}, vol_ratio={summary['vol_ratio']}")

        # Save raw CSV
        hist.to_csv(SPIKES_DIR / f"prices_{sym}.csv")
        print(f"  Saved: data/spikes/prices_{sym}.csv")

    except Exception as e:
        results[sym] = {"status": "ERROR", "error": str(e)}
        print(f"  ✗ Error: {e}")

# Save summary
out_path = SPIKES_DIR / "spike_yfinance_results.json"
with open(out_path, "w") as f:
    json.dump(results, f, indent=2)

print(f"\n{'='*50}")
print("SUMMARY:")
for sym, r in results.items():
    print(f"  {sym}: {r['status']} — {r.get('date_range', r.get('error', ''))}")
print(f"\nSaved: {out_path}")
