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

## Phase 4 — Push frontend — 2026-11-15

### D-13: New /api/portfolio endpoint; unseen filings = client's last-view timestamp
GET /api/portfolio?since=ISO returns per stock: name (parsed from cached screener HTML <title>, "share price" suffix stripped), last close, day %, 20d volume ratio, 30-day sparkline closes, unseen_filings (announcements since `since`; defaults to 72h when absent). The client keeps seen event ids / last-view in localStorage — no invented "seen" state server-side.

### D-14: Frontend stack choices
Vite + React + TS + Tailwind v4 (@tailwindcss/vite), no router (tab state), no chart lib (hand-rolled SVG sparklines), IBM Plex Sans/Mono via Google Fonts, dark default with light via prefers-color-scheme. Priority encoded as left-rail colour + card size, not badges. auto-widen: empty window escalates 24h→3d→7d→30d with a visible note. dist/ committed (snapshot-first deploy: FastAPI serves web/dist at "/" via StaticFiles, API stays under /api).

## Freshness + push fixes — 2026-11-15

### D-15: Date-ranged NSE cache keys include the date range
Old key `ann_{sym}_{days}d` served 6h-cached data for a *new* 30-day window forever. Keys are now `ann_{sym}_{from}_{to}`: a new range always re-fetches; only same-day re-runs reuse. Prices: deleted raw CSVs to force the re-pull (one-off; 6h TTL kept).

### D-16: ts_iso column — NSE ts doesn't sort
`ts` is "DD-Mon-YYYY HH:MM:SS"; string-comparing it against ISO cutoffs silently broke every window filter and the anomaly join. Added `ts_iso` (migrated + backfilled in init_db), digest/alerts filter on it, windows anchored to real now, and `/api/digest` returns `data_as_of` (latest announcement/price). Real max ts was 13-Nov (string MAX said 01-Nov — lexical trap).

### D-17: Classifier is subject-first; body/voiceSummary demoted to last resort
Old code substring-matched NSE_DESC_MAP over the filing *body*, so "board meeting"→results fired on 39 false positives and desc/subject got swapped. Now: ordered SUBJECT_RULES on the subject line (NSE desc IS the subject) → body keywords → voice summary. KEYWORD_RULES has no results pattern by design — "results" in a summary can never classify a filing. "Outcome of Board Meeting" checks the body for "financial result" (NSE subjects are terse). Meets/calls: investor_meetings by default, earnings_call on transcript/audio/schedule. "General Updates" stays unclassified. Reclassified all 231 (before: 84 unclassified/55 investor/39 results(false)/13 earnings…; after: 77/68/21/14/11/11/9/8/5/4/4 — results now 14 *real* filings).

### D-18: Digest = clusters + relative tiering, not stored tiers
Same company+category within 24h collapses into one item (lead = highest-scored child; ties: category base, then recency); `child_count`/`children` in the API, "xN" chip expands in the UI. Tiers computed per request within the window: must-see = top 15% of clusters (min 3, max 10, ≤3 per company), worth-a-look = next 25%, rest routine. Replaces static score thresholds (D-11) which didn't scale across window sizes. Per-filing price context (day %, vol ratio) now computed at the filing's date — after-close filings use the next trading day, missing data renders nothing (was: latest-day price for every item, and a silently-broken date join).

### D-19: Alerts UX — grouped, 7-day unread
Category alerts group to one row per company per day (expandable); unread badge counts only unseen events fired in the last 7 days. App capped at 480px centered with neutral surround ≥520px (sheet included). Untracked data/finn.db now committed (snapshot-first deploy needs it); WAL checkpointed.

## Deploy — 2026-11-15

### D-20: FINN_MODE=snapshot; runtime-only requirements; single container
`FINN_MODE=snapshot` (Dockerfile default) forces LLM_ENABLED=0 and makes POST /api/refresh return "live refresh disabled in hosted demo, data as of <date>" instead of touching NSE/screener. requirements.txt pins runtime-only deps (fastapi, uvicorn, pydantic, python-dotenv, requests); excluded yfinance, beautifulsoup4, lxml, pandas, numpy, PyMuPDF — they're local-refresh-only and never imported at app runtime (verified: fresh venv, 20 packages, all endpoints + /health + / + gated /api/refresh OK with no .env). Docker: python:3.12-slim, copies backend + data/finn.db + portfolio.json + web/dist; DB auto-copies to a temp path if FS is read-only. Docker not installed locally — verified via venv only. README + DEPLOY.md (Render free tier + HF Spaces) written.

## Demo clock + results card + card content — 2026-11-15

### D-21: Demo clock — all windows anchor to data_as_of EOD, not real now
Weekend/stale data made real-now windows empty and forced auto-widen on every load. Digest cutoff = data_as_of day 23:59:59 minus window; /api/meta added for the alerts badge (7d of data_as_of, caps "99+"); portfolio unseen counts clamp `since` to data_as_of and compare on ts_iso. Widened note now clears on non-widened loads (was: stale forever) and the highlighted chip always equals the window shown. Header: chips moved to their own wrapped row (was: squeezed beside the h1, clipped at 390px).

### D-22: Results card = screener standardised data, zero LLM
Per-filing quarter matched from filing text ("...ended 30th September 2026", 3 patterns); fallback = latest quarter-end <= filing date. Rows Revenue/EBITDA/PBT/PAT + QoQ/YoY % and abs + margins, all from `screener_fundamentals` (consolidated basis detected from which raw HTML variant was cached). Banks/NBFCs (no Operating Profit, Revenue+ present): EBITDA row renders n/a with note; unfiled quarters: latest shown + "not yet in standardised data". Verified against HAL's filing: Sales+ 7613cr = standalone total income 7,61,311 lakh in the PDF summary. Rejected: NSE financial-results endpoint (needs live network; snapshot mode must not).

### D-23: Card headlines clamped; results-mentioning bodies linked; empty cards demoted
voiceSummary clamped to 2 lines with "more" (walls of text were breaking the 10-second test). Bodies mentioning financial results get "Financial results filed — see results card" + a chip that filters to that symbol's results. Clusters with no voice summary can no longer tier above routine (was: bare "Outcome of Board Meeting" in Must-see). Tier math unchanged otherwise (D-18 relative ranking).

### D-24: Alert reasons computed at read time, not scan time
Enrichment in /api/alerts/events + dryrun: company name (screener title, html-unescaped), one-line reason built from kind/value + same-day price move + results-filing check ("Volume 2.5x 20-day average, price +4.9%"). Stored messages left untouched — re-scanning to rewrite 147 rows would have churned the snapshot for cosmetics. Full "31 Oct 2026" dates rendered client-side.

## Tiering sanity + card detail — 2026-11-15

### D-25: Must-see = top 15% AND absolute floor 9.0; no min-3 padding
The min-3 floor promoted quiet-window filler (credit reaffirmations, re-appointments) to must-see. Now: top 15% of clusters **and** score >= 9.0 — the floor was picked from the data: results/M&A/senior-mgmt clusters score 9.9–12.9, real fund raises 7.2, credit reaffirms 7.0, re-appointments 2.6, routine paperwork ≤0.5; 9.0 sits in the natural gap. A 24h window may have 1 (current data) or 0 must-see; header then reads "0 must-see, quiet window" and Worth-a-look leads. Also: ESOP/ESOS/ESPS allotment subjects, trading-window closures and compliance/duplicate certificates reclassified to unclassified (base 0.5) instead of fund_raise (7.0); bare "sebi" keyword replaced with "sebi order/notice/raid" (compliance certificates were getting +3.0); re-appointments demoted to base 2.5 and checked against subject+voiceSummary (subjects are terse); senior-role boost now reads voice text too.

### D-26: Results card — inline deltas + deterministic takeaway line
QoQ/YoY cells render "▲ +14.8%" on one nowrap line (tabular-nums, fits 390px). New rule-based takeaway under the table ("Revenue +14.8% YoY, PAT +16.0% YoY, EBITDA margin contracted 1.0pp vs last year") computed in results_summary.py from screener data — no LLM; margin delta vs same quarter last year, ±0.3pp dead-band.

### D-27: Management-change extraction is regex, not LLM
person/designation/action/effective-date parsed from subject+voiceSummary+body (honorific name tokens incl. initials, "as <role>" capture with stop-words, "effective/w.e.f/with effect from" date, normalised "23 Nov 2026"). Rendered "PERSON — ROLE — ACTION — effective DATE" above the clamped summary on cards + sheet, plus a "Look up background ↗" link that opens a quoted web search (person + company + role) in a new tab — no scraping, no invented bios; extraction failure falls back to summary only. Verified on 3 real filings (HAL CEO appointment, HDFCBANK re-appointment, Tata Steel retirement).

### D-28: Alert inbox — human reasons, ISO event dates, 30-day + 7-day windows
Reasons: "Volume 3.26x 20-day avg, price -2.1% (results filed)" for price/volume rules, "<Category>: <filing one-liner>" for category rules (label falls back to the category name). Events filtered to 30 days of data_as_of server-side; badge already unread-within-7-days capped 99+. Found and fixed stale rows from the pre-D-16 scan: 67 events carried NSE "DD-Mon-YYYY" dates (one truncated) and 36 duplicated newer ISO rows — dates re-derived from announcements.ts_iso, duplicates deleted, one orphan removed. ORDER BY date DESC (was fired_at DESC, which is the scan timestamp and identical for all rows).

### D-29: Gateway hardening for local batch use — 2.5 flash retired, 3.8 hangs on OpenAI-compat
Gemini deprecated the OpenAI-compat endpoint for 2.5-flash ("no longer available to new users" → 404) and the newest 3.8-flash **hangs indefinitely** on /chat/completions (needs the new Interactions API) — a silent infinite stall, not an error. /models probed live; picked current working Flash pair: CHEAP=gemini-3.1-flash-lite (1.4s), STRONG=gemini-3.5-flash (1.9s). Gateway now: IPv4 forced (urllib3 HAS_IPV6=False), plain payloads (response_format + reasoning_effort removed), JSON contract by prompt ("Return ONLY a JSON object") + fence-strip + pydantic validate, one validation retry with the error fed back, 90s timeout, exp backoff on 429/5xx, 4s throttle between sequential calls. scripts/llm_check.py: extract 11.1s / summary 2.2s / cache-repeat 0.00s — PASS. FINN_MODE=snapshot untouched (LLM_ENABLED=0 at config; gateway never imported at app runtime).

## Research dossier (deterministic pull slice) — 2026-11-15

### D-30: Dossier = pure screener-table math, /api/research/{symbol}, zero LLM/network
Four sections (snapshot tiles+sparklines, 6y trajectory bars+CAGR+margin trend, balance sheet, CFO-vs-profit cash quality) computed from `screener_fundamentals` in one SQLite read — works identically in snapshot mode. CAGR only when >=4 non-null FYs and first >0 (else "n/a"); null renders "not available" everywhere. Bank handling: Financing Margin %/ROE rows replace OPM/ROCE with an asterisk note; ROCE tile says "not disclosed for banks"; cash flag suppressed for banks (loan growth sits in investing, so CFO<profit is structural, not a warning) with the reason shown. HDFCBANK's screener financing margin is genuinely -10% — shown as-is with note rather than hidden. TCS/SUZLON have no ROE row in screener's ratios → honest "not available". Cash-quality flag: cumulative CFO < 70% of cumulative PAT over >=3 FYs. Ticker chips: portfolio order first, then any other ingested symbol; section headers carry tappable plain-English tooltips (title-attr is useless on touch). Rejected: LLM summaries/interpolating missing ROE — violates rule 4.
