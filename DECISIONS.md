# DECISIONS.md — FINN build log

## Phase 0 — 2026-11-15

### D-01: Use plain `requests` for NSE, not curl_cffi
Both work. Homepage returns 403 on plain requests but the API endpoint itself returns 200 regardless.
curl_cffi gets 200 on homepage and 200 on API. Either works from local machine.
**Decision:** Use plain `requests` for NSE announcements (simpler, no extra dep). Keep curl_cffi as cloud-deploy fallback since datacenter IPs may need browser impersonation.

### D-02: NSE structured financial-results endpoint confirmed — prefer over PDF
`/api/corporates-financial-results?index=equities&symbol=<SYM>&period=Quarterly` returns 200 and a list of ~130-162 items per company with XBRL links and filing metadata. XBRL link is in `xbrl` field.
**Decision:** Use NSE structured endpoint + XBRL for results cards. Fall back to PDF→LLM only if XBRL is null or parse fails.
**What was rejected:** Pure PDF-first approach (more LLM cost, more brittle).

### D-03: BSE API endpoint blocked (400/403)
`api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w` returns 400 (curl_cffi) or 403 (requests). The API appears to require CORS validation or additional session state not easily reproducible.
**Decision:** Drop BSE as a live data source. NSE is sufficient (all 18 portfolio companies listed on NSE). Screener.in documents section lists BSE filing links (xml-data/corpfiling/AttachHis/*.pdf) which ARE downloadable — use those for PDFs.
**What was rejected:** Reverse-engineering BSE session (high effort, unstable).

### D-04: Screener documents section = gateway to BSE PDFs
Screener parses BSE's documents and renders links to `bseindia.com/xml-data/corpfiling/AttachHis/*.pdf` (AR) and `bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname=*.pdf` (concalls/announcements). Both download fine with a Referer: screener.in header.
**Decision:** Scrape screener for doc links. Download PDFs from BSE via those links. This gives us both ARs and concall transcripts without needing to parse BSE API directly.

### D-05: NSE voiceSummary field — free pre-summarised text
NSE announcement objects include `voiceSummary` (1-2 sentence TTS summary) and `desc` (category hint). Using these reduces LLM classification calls significantly.
**Decision:** Use `desc` field for rule-based category mapping first. Use `voiceSummary` as the display summary for routine/low-priority items (no LLM). Only send to LLM if category is ambiguous AND item is above minimum priority threshold.

### D-06: LLM gateway — Hyde OpenAI-compatible endpoint (primary)
Gemini API key provided (`AIzaSy...`) but `generativelanguage.googleapis.com` times out from this network. Hyde gateway (`inference.hyde.tech/v1`) confirmed working in Phase 0.
Models available: `hyde/glm-5.3-flash` (cheap tier, reasoning model, JSON mode works) and `hyde/kimi-k3` (strong tier).
**Decision:** Use Hyde as primary LLM gateway. Gateway uses standard OpenAI `chat/completions` interface. Gemini key stored in `.env` for future use if network allows.
**What was rejected:** Gemini direct API (network timeout); no-LLM approach (needed for enrichment).

### D-07: All 18 portfolio tickers verified on yfinance `.NS`
Every ticker resolves cleanly including ETERNAL (ex-Zomato). No swaps needed.

### D-08: Snapshot-first deploy strategy
NSE/BSE commonly block datacenter IPs (confirmed in spec). Strategy: build local snapshot with 30-day data, commit sqlite + raw cache to repo, deployed app serves snapshot and shows "as of <date>". Refresh endpoint attempts live fetch with graceful fallback.
**What was rejected:** Live-only approach (would fail on cloud deploy).

## Phases 1+2 completion — 2026-11-15

### D-09: Force IPv4-first DNS globally
Python requests stalled ~2x timeout on every HTTPS call; this network's IPv6 blackholes and urllib3 has no happy-eyeballs (curl unaffected). Patched `socket.getaddrinfo` in `backend/config.py` to sort AF_INET first. NSE calls went from 63s to ~2s.

### D-10: LLM hardening; LLM_ENABLED=0 shipped for now
`gemini-2.5-flash` returns 404 "no longer available to new users"; .env models moved to `gemini-3.8-flash`. Gateway now: requests + hard 60s timeout, `reasoning_effort=none` with auto-strip on 400, exp backoff on 429/5xx, SQLite cache by hash, ledger. Deep calls with json mode still hung at the endpoint during this session, so `.env` ships `LLM_ENABLED=0`: classification/ingestion run rules-only (84/233 unclassified, tiered routine). Flag flip to 1 re-enables LLM without code changes.

### D-11: Priority tier thresholds re-tuned on real score distribution
Spec default (must_see>=12) left must_see empty: results/M&A cluster at 10.1-10.6. Re-set to must_see>=9.5, worth_a_look>=4.0. Digest 7d: 22/11/63.

### D-12: yfinance via Ticker().history(), not download()
`yf.download` (single ticker) returns MultiIndex columns; row indexing crashed. `Ticker.history` returns flat columns and is what the 2160-row snapshot was built with.
