"""
Spike: NSE corporate announcements + NSE financial-results endpoint.
Tests:
  1. Session-based requests to NSE API (homepage cookie grab first)
  2. curl_cffi browser impersonation as fallback
  3. NSE financial-results endpoint (structured quarterly results)
  4. Saves raw JSON to data/spikes/
"""
import json
import os
import sys
import time
from pathlib import Path

SPIKES_DIR = Path(__file__).parent.parent / "data" / "spikes"
SPIKES_DIR.mkdir(parents=True, exist_ok=True)

TICKERS = ["RELIANCE", "TCS", "ETERNAL"]
RESULTS_STATUS = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
}


def get_nse_session():
    """Establish NSE session by hitting homepage first."""
    import requests
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        print("  Getting NSE homepage for session cookies...")
        r = s.get("https://www.nseindia.com/", timeout=15)
        print(f"  Homepage: {r.status_code}, cookies: {list(r.cookies.keys())}")
        time.sleep(2)
        return s, None
    except Exception as e:
        return None, str(e)


def probe_nse_announcements_requests(sym):
    """Try NSE announcements via requests session."""
    import requests
    s, err = get_nse_session()
    if not s:
        return None, f"session failed: {err}"

    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={sym}"
    try:
        r = s.get(url, timeout=15)
        print(f"  NSE announcements {sym} [requests]: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            # Save raw
            out = SPIKES_DIR / f"nse_announcements_{sym}_requests.json"
            with open(out, "w") as f:
                json.dump(data, f, indent=2)
            count = len(data) if isinstance(data, list) else "?"
            print(f"  ✓ Got {count} announcements. Saved {out.name}")
            return data, None
        else:
            return None, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return None, str(e)


def probe_nse_announcements_curl(sym):
    """Try NSE announcements via curl_cffi (browser impersonation)."""
    try:
        from curl_cffi import requests as cr
        url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={sym}"

        # First hit homepage
        print("  curl_cffi: hitting NSE homepage...")
        s = cr.Session(impersonate="chrome120")
        r = s.get("https://www.nseindia.com/", timeout=15)
        print(f"  Homepage: {r.status_code}")
        time.sleep(2)

        r2 = s.get(url, timeout=15, headers={"Referer": "https://www.nseindia.com/"})
        print(f"  NSE announcements {sym} [curl_cffi]: {r2.status_code}")
        if r2.status_code == 200:
            data = r2.json()
            out = SPIKES_DIR / f"nse_announcements_{sym}_curl.json"
            with open(out, "w") as f:
                json.dump(data, f, indent=2)
            count = len(data) if isinstance(data, list) else "?"
            print(f"  ✓ Got {count} announcements. Saved {out.name}")
            return data, None
        else:
            return None, f"HTTP {r2.status_code}: {r2.text[:200]}"
    except Exception as e:
        return None, str(e)


def probe_nse_results(sym):
    """Probe NSE structured financial-results endpoint."""
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome120")
        # Get session
        s.get("https://www.nseindia.com/", timeout=15)
        time.sleep(2)

        url = f"https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={sym}&period=Quarterly"
        r = s.get(url, timeout=15, headers={"Referer": "https://www.nseindia.com/"})
        print(f"  NSE financial-results {sym}: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            out = SPIKES_DIR / f"nse_results_{sym}.json"
            with open(out, "w") as f:
                json.dump(data, f, indent=2)
            count = len(data) if isinstance(data, list) else (len(data.get("data", [])) if isinstance(data, dict) else "?")
            print(f"  ✓ Got financial results (count~{count}). Saved {out.name}")
            # Peek at first item
            if isinstance(data, list) and data:
                print(f"  First item keys: {list(data[0].keys())[:8]}")
            elif isinstance(data, dict):
                print(f"  Response keys: {list(data.keys())[:8]}")
            return data, None
        else:
            return None, f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return None, str(e)


print("=" * 60)
print("PROBE 1: NSE Announcements via requests")
print("=" * 60)
for sym in TICKERS[:2]:  # Try 2 to save time
    print(f"\n--- {sym} ---")
    data, err = probe_nse_announcements_requests(sym)
    RESULTS_STATUS[f"nse_ann_requests_{sym}"] = "OK" if data else f"FAIL: {err}"
    time.sleep(2)

print("\n" + "=" * 60)
print("PROBE 2: NSE Announcements via curl_cffi")
print("=" * 60)
for sym in TICKERS:
    print(f"\n--- {sym} ---")
    data, err = probe_nse_announcements_curl(sym)
    RESULTS_STATUS[f"nse_ann_curl_{sym}"] = "OK" if data else f"FAIL: {err}"
    time.sleep(2)

print("\n" + "=" * 60)
print("PROBE 3: NSE Financial Results (structured)")
print("=" * 60)
for sym in TICKERS[:2]:
    print(f"\n--- {sym} ---")
    data, err = probe_nse_results(sym)
    RESULTS_STATUS[f"nse_results_{sym}"] = "OK" if data else f"FAIL: {err}"
    time.sleep(2)

# Save summary
out = SPIKES_DIR / "spike_nse_results.json"
with open(out, "w") as f:
    json.dump(RESULTS_STATUS, f, indent=2)

print("\n" + "=" * 60)
print("NSE PROBE SUMMARY:")
for k, v in RESULTS_STATUS.items():
    icon = "✓" if v == "OK" else "✗"
    print(f"  {icon} {k}: {v}")
