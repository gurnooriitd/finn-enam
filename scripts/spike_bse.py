"""
Spike: BSE corporate announcements for RELIANCE.
BSE scrip code for RELIANCE is 500325.
Tests the BseIndiaAPI endpoint with Origin/Referer headers.
"""
import json
import time
from pathlib import Path

SPIKES_DIR = Path(__file__).parent.parent / "data" / "spikes"
SPIKES_DIR.mkdir(parents=True, exist_ok=True)

# BSE scrip codes for test symbols
BSE_CODES = {
    "RELIANCE": "500325",
    "TCS": "532540",
    "ETERNAL": "543320",  # Zomato/Eternal
}

RESULTS = {}

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
}


def probe_bse_announcements_curl(sym, scrip_code):
    """BSE announcements via curl_cffi."""
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome120")

        # Hit BSE homepage first for cookies
        print(f"  Getting BSE homepage for cookies...")
        r0 = s.get("https://www.bseindia.com/", timeout=15, headers=BSE_HEADERS)
        print(f"  Homepage: {r0.status_code}, cookies: {list(r0.cookies.keys())[:5]}")
        time.sleep(1.5)

        # Try the main announcements endpoint
        url = (
            f"https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?"
            f"pageno=1&strCat=-1&strPrevDate=&strScrip={scrip_code}&strSearch=P&strToDate=&strType=C&subcategory=-1"
        )
        print(f"  Fetching BSE announcements for {sym} ({scrip_code})...")
        r = s.get(url, timeout=15, headers=BSE_HEADERS)
        print(f"  Status: {r.status_code}")

        if r.status_code == 200:
            data = r.json()
            out = SPIKES_DIR / f"bse_announcements_{sym}.json"
            with open(out, "w") as f:
                json.dump(data, f, indent=2)
            # Peek
            items = data.get("Table", data.get("data", data if isinstance(data, list) else []))
            count = len(items) if isinstance(items, list) else "?"
            print(f"  ✓ Got ~{count} announcements. Saved {out.name}")
            if isinstance(items, list) and items:
                print(f"  First item keys: {list(items[0].keys())[:8]}")
            return data, None
        else:
            body = r.text[:300]
            return None, f"HTTP {r.status_code}: {body}"
    except Exception as e:
        return None, str(e)


def probe_bse_requests(sym, scrip_code):
    """BSE via plain requests."""
    import requests
    s = requests.Session()
    s.headers.update(BSE_HEADERS)
    try:
        s.get("https://www.bseindia.com/", timeout=15)
        time.sleep(1)
        url = (
            f"https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?"
            f"pageno=1&strCat=-1&strPrevDate=&strScrip={scrip_code}&strSearch=P&strToDate=&strType=C&subcategory=-1"
        )
        r = s.get(url, timeout=15)
        print(f"  BSE [requests] {sym}: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            out = SPIKES_DIR / f"bse_announcements_{sym}_requests.json"
            with open(out, "w") as f:
                json.dump(data, f, indent=2)
            return data, None
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return None, str(e)


print("=" * 60)
print("PROBE: BSE Announcements")
print("=" * 60)

for sym, code in list(BSE_CODES.items())[:2]:  # Test 2 symbols
    print(f"\n--- {sym} (scrip: {code}) ---")
    print("  [curl_cffi]")
    data, err = probe_bse_announcements_curl(sym, code)
    RESULTS[f"bse_curl_{sym}"] = "OK" if data else f"FAIL: {err}"

    if not data:
        print("  [requests fallback]")
        data2, err2 = probe_bse_requests(sym, code)
        RESULTS[f"bse_requests_{sym}"] = "OK" if data2 else f"FAIL: {err2}"
    time.sleep(2)

out = SPIKES_DIR / "spike_bse_results.json"
with open(out, "w") as f:
    json.dump(RESULTS, f, indent=2)

print("\n" + "=" * 60)
print("BSE PROBE SUMMARY:")
for k, v in RESULTS.items():
    icon = "✓" if v == "OK" else "✗"
    print(f"  {icon} {k}: {v}")
