"""
Spike: screener.in
Tests:
  1. Fetch company page (consolidated), extract quarterly results table
  2. Extract Documents section (AR + concall links)
  3. Download 1 AR PDF and 1 concall PDF, extract text with PyMuPDF
  4. Check text quality (is it searchable or scanned?)
"""
import json
import re
import time
import hashlib
from pathlib import Path

SPIKES_DIR = Path(__file__).parent.parent / "data" / "spikes"
DOCS_DIR = Path(__file__).parent.parent / "data" / "raw" / "docs"
SPIKES_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)

TICKERS = ["RELIANCE", "TCS", "INFY"]
RESULTS = {}

HEADERS = {
    "User-Agent": "FINN/0.1 (financial research tool; contact@example.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_screener_page(sym):
    """Fetch screener company page, try consolidated then standalone."""
    import requests
    s = requests.Session()
    s.headers.update(HEADERS)
    for path in ["consolidated", ""]:
        url = f"https://www.screener.in/company/{sym}/{path}/" if path else f"https://www.screener.in/company/{sym}/"
        try:
            r = s.get(url, timeout=20)
            print(f"  screener {sym} [{path or 'standalone'}]: {r.status_code} ({len(r.text)} chars)")
            if r.status_code == 200 and "Quarterly Results" in r.text:
                out = SPIKES_DIR / f"screener_{sym}_{path or 'standalone'}.html"
                out.write_text(r.text, encoding="utf-8")
                print(f"  Saved: {out.name}")
                return r.text, url, None
        except Exception as e:
            print(f"  Error: {e}")
    return None, None, "failed all paths"


def parse_quarterly_table(html):
    """Extract quarterly results table rows."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Find the quarterly results section
        qr_section = None
        for section in soup.find_all("section"):
            if section.get("id") == "quarters":
                qr_section = section
                break

        if not qr_section:
            # Try searching for heading
            for h2 in soup.find_all("h2"):
                if "Quarterly" in h2.text:
                    qr_section = h2.find_parent("section")
                    break

        if not qr_section:
            return None, "quarterly section not found"

        table = qr_section.find("table")
        if not table:
            return None, "no table in quarterly section"

        rows = []
        headers = [th.text.strip() for th in table.find_all("th")]
        for tr in table.find_all("tr"):
            cells = [td.text.strip() for td in tr.find_all("td")]
            if cells:
                rows.append(cells)

        return {"headers": headers, "rows": rows[:10]}, None
    except Exception as e:
        return None, str(e)


def parse_documents(html):
    """Extract document links (AR + concall) from screener."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        docs = {"annual_reports": [], "concalls": []}

        # Find documents section
        doc_section = None
        for section in soup.find_all("section"):
            h = section.find(["h2", "h3"])
            if h and "document" in h.text.lower():
                doc_section = section
                break

        if not doc_section:
            # Try looking for specific link patterns
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.text.strip().lower()
                if "annual" in text or "annual" in href.lower():
                    docs["annual_reports"].append({"text": a.text.strip(), "url": href})
                elif "concall" in text or "transcript" in text or "concall" in href.lower():
                    docs["concalls"].append({"text": a.text.strip(), "url": href})
            return docs, None

        for a in doc_section.find_all("a", href=True):
            href = a["href"]
            text = a.text.strip()
            text_lower = text.lower()
            if "annual" in text_lower or ".pdf" in href.lower():
                docs["annual_reports"].append({"text": text, "url": href})
            elif "concall" in text_lower or "transcript" in text_lower or "earnings" in text_lower:
                docs["concalls"].append({"text": text, "url": href})

        return docs, None
    except Exception as e:
        return None, str(e)


def download_pdf(url, name):
    """Download a PDF and return local path."""
    import requests
    if not url.startswith("http"):
        url = "https://www.screener.in" + url
    try:
        r = requests.get(url, timeout=30, headers=HEADERS, stream=True)
        if r.status_code == 200 and "pdf" in r.headers.get("content-type", "").lower():
            path = DOCS_DIR / name
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            print(f"  Downloaded PDF: {path.name} ({path.stat().st_size // 1024}KB)")
            return path, None
        return None, f"HTTP {r.status_code}, content-type: {r.headers.get('content-type')}"
    except Exception as e:
        return None, str(e)


def extract_pdf_text(path):
    """Extract text with PyMuPDF."""
    import fitz  # pymupdf
    doc = fitz.open(str(path))
    n_pages = len(doc)
    sample_pages = []
    char_counts = []

    for i in range(min(5, n_pages)):
        page = doc[i]
        text = page.get_text()
        char_counts.append(len(text))
        sample_pages.append({"page": i + 1, "chars": len(text), "preview": text[:200].replace("\n", " ")})

    avg_chars = sum(char_counts) / len(char_counts) if char_counts else 0
    is_searchable = avg_chars > 100  # scanned PDFs have ~0 chars per page

    return {
        "n_pages": n_pages,
        "avg_chars_per_page": round(avg_chars, 1),
        "is_searchable": is_searchable,
        "sample_pages": sample_pages,
    }


print("=" * 60)
print("PROBE: screener.in")
print("=" * 60)

downloaded_pdfs = []

for sym in TICKERS:
    print(f"\n--- {sym} ---")
    html, url, err = fetch_screener_page(sym)

    if not html:
        RESULTS[f"screener_{sym}"] = f"FAIL: {err}"
        continue

    # Parse quarterly table
    qt, qt_err = parse_quarterly_table(html)
    if qt:
        print(f"  ✓ Quarterly table: {len(qt['rows'])} rows, headers: {qt['headers'][:5]}")
        RESULTS[f"screener_quarterly_{sym}"] = f"OK ({len(qt['rows'])} rows)"
    else:
        print(f"  ✗ Quarterly table: {qt_err}")
        RESULTS[f"screener_quarterly_{sym}"] = f"FAIL: {qt_err}"

    # Parse documents
    docs, docs_err = parse_documents(html)
    if docs:
        ar_count = len(docs["annual_reports"])
        cc_count = len(docs["concalls"])
        print(f"  ✓ Documents: {ar_count} ARs, {cc_count} concalls")
        RESULTS[f"screener_docs_{sym}"] = f"OK ({ar_count} ARs, {cc_count} concalls)"
        # Save docs list
        (SPIKES_DIR / f"screener_docs_{sym}.json").write_text(json.dumps(docs, indent=2))
        downloaded_pdfs.extend([(sym, d, "AR") for d in docs["annual_reports"][:1]])
        downloaded_pdfs.extend([(sym, d, "concall") for d in docs["concalls"][:1]])
    else:
        RESULTS[f"screener_docs_{sym}"] = f"FAIL: {docs_err}"

    time.sleep(1.5)

# Try to download 1 AR and 1 concall
print("\n--- PDF Downloads ---")
pdf_results = []
for sym, doc, kind in downloaded_pdfs[:4]:  # limit downloads
    url = doc["url"]
    name = f"{sym}_{kind}_{hashlib.md5(url.encode()).hexdigest()[:8]}.pdf"
    print(f"  Downloading {kind} for {sym}: {doc['text'][:60]}")
    path, err = download_pdf(url, name)
    if path:
        txt = extract_pdf_text(path)
        pdf_results.append({
            "sym": sym,
            "kind": kind,
            "url": url,
            "file": name,
            **txt,
        })
        RESULTS[f"pdf_{kind}_{sym}"] = f"OK (n_pages={txt['n_pages']}, searchable={txt['is_searchable']}, avg_chars={txt['avg_chars_per_page']})"
        print(f"  ✓ {txt['n_pages']} pages, avg_chars={txt['avg_chars_per_page']}, searchable={txt['is_searchable']}")
        print(f"  Preview p1: {txt['sample_pages'][0]['preview'][:120]}")
    else:
        RESULTS[f"pdf_{kind}_{sym}"] = f"FAIL: {err}"
        print(f"  ✗ {err}")
    time.sleep(1.5)

# Save full results
(SPIKES_DIR / "spike_screener_results.json").write_text(json.dumps(RESULTS, indent=2))
(SPIKES_DIR / "spike_screener_pdfs.json").write_text(json.dumps(pdf_results, indent=2))

print("\n" + "=" * 60)
print("SCREENER PROBE SUMMARY:")
for k, v in RESULTS.items():
    icon = "✓" if v.startswith("OK") else "✗"
    print(f"  {icon} {k}: {v}")
