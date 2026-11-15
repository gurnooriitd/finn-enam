# FINN: build spec for the coding agent

You are building **FINN**, a mobile-first financial investment agent, for the ENAM AI Engineer Build Challenge.
Deadline: **15 Nov 2026**. Assume roughly one working day. Read this whole file before writing code.

## 0. Rules of engagement (non-negotiable)

1. **A demo that doesn't run is the worst outcome.** Thin-but-working beats broad-but-broken. Every phase must end in a runnable state.
2. **Work one phase at a time.** After each phase: run it, show real output as proof, commit, then STOP and wait for the user to say "next".
3. **Minimal diffs.** After Phase 1, change only what the task needs. No drive-by rewrites or reformatting.
4. **Never fabricate data.** No fake numbers, no invented filings, no made-up bios. If something is synthetic, label it `synthetic` in the data and in the UI. If a source is unavailable, say so in the UI ("not disclosed in sources", "unverified").
5. **Probe before you build.** Every external endpoint in this file is described from memory and may have changed. Phase 0 verifies them with real requests. Trust the probe, not this file.
6. **Cache everything raw to disk** (`data/raw/`), keyed by URL or content hash, so re-runs cost nothing and nothing breaks if a site blocks us later.
7. **Every LLM call goes through one gateway** (`llm/gateway.py`) that handles model routing, JSON-schema validation (pydantic), retries, caching and the cost ledger. No direct SDK calls elsewhere.
8. Secrets live in `.env`. Ship `.env.example`. Never commit keys.
9. Keep a running `DECISIONS.md` (append one short entry per non-obvious decision or trade-off: what, why, what was rejected). The user needs it for the covering email.
10. Complete, copy-pasteable files when you create something new. Targeted edits when you modify.

## 1. What is being graded

Creativity, product sense, judgment, architecture, and efficiency (cost per result). Not completeness or polish. Density and design count. "No AI slop": concise, interactive, opinionated, nothing that looks like a model filling a template.

Deliverables: mobile-first deployed prototype, GitHub repo, AI build history, short writeup.

## 2. Product point of view

**"Triage, then interrogate."** An investor opens FINN and in 10 seconds knows what matters. Then they can interrogate any item.

- **Home is a Briefing Stream, not a dashboard.** A header says things like "34 filings overnight → 4 matter". Below it are three lanes: **Must-see** (expanded cards), **Worth a look** (one-liners), **Routine** (collapsed, grouped by type with counts).
- **Every card is tappable** and opens a bottom sheet: the enriched detail plus 3 context-aware suggested questions plus a free-text ask. This unifies push and pull: the ask bar is always scoped to what you're looking at.
- **A global Ask bar** accepts a ticker or a natural-language question. An intent router decides: research a company, query the digest, compare, or configure an alert ("alert me when Suzlon volume is 3x").
- **Stretch:** hold-to-talk using the browser Web Speech API ("what changed overnight?"). It is free and distinctive.

Push = Briefing + Alerts. Pull = Company Dossier + Ask.

## 3. Architecture and stack

Pick this unless Phase 0 shows a blocker:

- **Backend:** Python 3.11, FastAPI, SQLite (via SQLModel or plain sqlite3), pydantic. One service.
- **Ingestion:** Python scripts in `ingest/`, run locally and via a "refresh" endpoint. They write to SQLite and `data/raw/`.
- **Frontend:** Vite + React + TypeScript + Tailwind, PWA (manifest, installable), built to static files and **served by FastAPI** so there is one deploy.
- **Streaming:** SSE for the Pull analysis, so sections appear progressively.
- **Deploy:** one Dockerfile, to Render, Railway or Fly. Commit a **data snapshot** (SQLite file plus cached extractions) so the deployed app works even if NSE/BSE/screener block the host's IP. They commonly block datacenter IPs.
- **No Streamlit, no Gradio.** They can't hit the design bar.

### Repo layout
```
finn/
  AGENTS.md  DECISIONS.md  README.md  .env.example  Dockerfile
  backend/
    main.py  config.py  db.py  models.py
    api/ (digest.py alerts.py research.py ask.py portfolio.py costs.py)
    ingest/ (prices.py nse.py bse.py screener.py docs.py)
    push/ (classify.py priority.py results_extract.py people.py alerts_scan.py)
    pull/ (retrieve.py sections.py guidance.py promised_vs_delivered.py)
    llm/ (gateway.py router.py ledger.py schemas.py)
  data/ (raw/ finn.db portfolio.json)
  web/  (Vite app)
  scripts/ (spike_*.py refresh.py warm_cache.py)
```

## 4. Companies

### Portfolio (18, a deliberate sector and size mix). VERIFY every ticker resolves
Tickers may have been renamed or demerged. Check each on yfinance (`SYMBOL.NS`) and screener, and swap any that fail.

RELIANCE, TCS, HDFCBANK, ICICIBANK, SUNPHARMA, LT, MARUTI, TITAN, ITC, BAJFINANCE, ETERNAL (ex-Zomato), DIXON, POLYCAB, HAL, SUZLON, TATASTEEL, ADANIPORTS, YESBANK

Give each a mock quantity or weight in `data/portfolio.json` (mark as mock). Weights feed priority scoring.

### Ad-hoc research demo set
INFY, ASIANPAINT, TRENT, PIDILITIND. Choose companies that have recent concall transcripts on screener.

### Generalisation test (mandatory)
Pull analysis must work for any NSE ticker where data exists. Before finishing, run it on one ticker not in any list above (e.g. DEEPAKNTR) and fix whatever breaks. The engine must never hard-code symbols.

## 5. Data layer

### 5.1 Prices (EOD)
- `yfinance`, `SYMBOL.NS` (fallback `.BO`). Pull about 120 trading days of OHLCV per portfolio stock, plus on-demand for any ticker.
- Table `prices(symbol, date, open, high, low, close, volume)`.
- Derived on read: day % change, volume ratio = today's volume / mean of the previous 20 sessions (exclude today).
- Handle splits and bonuses (use adjusted close for % moves, and note it).

### 5.2 Announcements (NSE / BSE)
Both are unofficial JSON endpoints behind bot protection. Phase 0 must probe them.
- **NSE:** `https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=<SYM>` (+ optional `from_date`/`to_date` as DD-MM-YYYY). Needs a session: GET the homepage first for cookies, send browser-like headers (User-Agent, Accept, Referer: https://www.nseindia.com/), reuse the session, sleep 1-2s between calls. If plain `requests` gets blocked, try `curl_cffi` with browser impersonation. Libraries like `nsepython` / `jugaad-data` exist; evaluate them but don't depend on unmaintained ones.
- **NSE results (probe this, could be a big win):** a financial-results endpoint (something like `/api/corporates-financial-results?index=equities&symbol=<SYM>&period=Quarterly`) that returns structured quarterly results plus an XBRL link. If it works, **prefer it over PDF parsing** for the results card, and use the LLM only as fallback.
- **BSE:** `https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?...` (needs the BSE scrip code and Origin/Referer headers `https://www.bseindia.com`). Use as fallback or cross-check.
- **Last-resort fallback:** screener.in company pages list recent announcements linking to the exchange PDFs. Probe whether it is scrapeable.
- Normalise into `announcements(id, symbol, ts, subject, raw_desc, attachment_url, exchange, category, priority, summary_json, ...)`. Dedupe on (exchange, exchange_id) or a hash of (symbol, ts, subject).
- Backfill about 30 days for the portfolio so the demo always has content (see 7.5).

### 5.3 Fundamentals (screener.in)
- `https://www.screener.in/company/<SYMBOL>/consolidated/` (fallback `/` for standalone). No login needed for visible content.
- Parse with BeautifulSoup/pandas: Quarterly results, Profit & Loss, Balance Sheet, Cash Flow, Ratios tables (store as tidy long-format).
- Parse the **Documents** section: annual report links and concall links (transcript / PPT / notes) with their period labels.
- Be polite: one request per ~1.5s, cache aggressively, descriptive but normal User-Agent.
- Docs: download PDFs to `data/raw/docs/`, extract text with PyMuPDF (page-aware), store `docs(symbol, kind, period, url, path, text_hash, n_pages)` and `doc_chunks(doc_id, page, section_hint, text)`. Skip scanned/no-text PDFs gracefully and record why.

## 6. Push spec

### 6.1 Classification (cheap first)
1. Rule-based classifier over subject/desc using the 17 categories below: keyword and regex rules, tuned against real data. NSE usually provides its own `desc` category, so map that too.
2. Only ambiguous items go to the cheap LLM (schema: `{category, confidence}`). Cache by text hash.

Categories (from the reference doc, adapt freely): results, M&A, fund_raise, management_change, litigation, insider_trading, earnings_call, investor_meetings, credit_rating, dividend, bonus_split, capex, press_release, postal_ballot, agm_egm, pledging, unclassified.

### 6.2 Priority scoring (deterministic, explainable)
`score = category_base x portfolio_weight_boost + interest_boost + anomaly_boost + keyword_boost`
- `category_base`: results and M&A highest; management_change (CEO/CFO/MD far above other directors); credit_rating (downgrade >> reaffirm); fund_raise; litigation; capex; dividend; pledging; and so on. Routine paperwork near zero.
- `interest_boost`: categories the user subscribed to.
- `anomaly_boost`: **the same stock had an unusual price/volume move on the filing day.** Cross-signal fusion is the product insight, so show it ("results + volume 3.1x").
- `keyword_boost`: resign, fraud, default, penalty, downgrade, insolvency, search and seizure, etc.
- Tiers: **Must-see / Worth a look / Routine**. Store the score breakdown and surface it in the sheet ("why this ranked high").

### 6.3 Per-type presentation (the judgment call)
- **results:** Headline card, see 6.4. Always.
- **management_change:** Who / role / effective date / reason if stated / predecessor / background bio, see 6.5.
- **credit_rating:** one line: agency, instrument, old to new rating, outlook. Flag downgrades.
- **dividend / bonus_split:** one line with amount, record date, and yield vs last close (deterministic, no LLM).
- **fund_raise, M&A, litigation, capex:** one-line LLM summary from the first ~1500 chars of the filing. Include amounts, counterparties, jurisdiction if present.
- **insider_trading / pledging:** deterministic one-liner (who, buy/sell, quantity, % of holding if parseable). Group repeats.
- **earnings_call, investor_meetings, agm_egm, postal_ballot, unclassified:** no LLM. Surface the filing link at low priority; collapse into Routine.

### 6.4 Results card (the showpiece)
- Source: the structured NSE results endpoint if available, else the results PDF.
- **Indian results filings contain three columns: current quarter, preceding quarter, same quarter last year.** One filing gives both QoQ and YoY. Don't go hunting for other filings.
- PDF path: PyMuPDF text, find the results table pages (keyword search "Total income", "Profit before tax"), send only those pages to the cheap LLM with a strict pydantic schema: `{basis: consolidated|standalone, unit: lakh|crore|million, revenue/ebitda/pbt/pat x {cur_q, prev_q, yoy_q}, finance_cost, depreciation, other_income}`.
- **EBITDA is usually not reported.** Derive: `EBITDA = PBT + finance costs + depreciation - other income`. Mark it `derived` in the UI with a tooltip showing the formula.
- **Validation (deterministic):** `PBT - tax ~= PAT` within tolerance, revenue > 0, units consistent across columns, margins in a sane range. If validation fails, show the numbers with an "unverified" chip instead of hiding them, and fall back to the screener quarterly table for cross-check.
- Banks and NBFCs have different formats (no EBITDA; use NII and PPOP). Handle by falling back to showing Revenue/PBT/PAT only, with a note. Don't force it.
- UI: 4 rows (Revenue, EBITDA, PBT, PAT), columns Q | QoQ% | YoY%, plus margin rows. Basis/unit chip. Link to the source filing.

### 6.5 Management-change card
- Extract (cheap LLM, schema): person, designation, action (appointed/resigned/retired), effective date, reason, predecessor.
- Enrich the **incoming** person: web search API (free tier: Tavily, Brave, or Serper; or DuckDuckGo if stable) for `"<name>" "<designation>" "<company>"`. LinkedIn scraping is blocked and against ToS, so don't do it. Use search snippets and LinkedIn result snippets, and summarise to 2-3 lines: prior roles and companies, tenure, education.
- **Hard rule:** the bio may only contain facts present in retrieved snippets. Show source links. Add a "web-sourced, verify" chip. If identity is ambiguous or nothing is found, say "couldn't verify background", never guess. Cache by (name, company).
- This is push-side enrichment and is explicitly allowed by the brief. The pull side is strictly primary-source (see 7.1).

### 6.6 Triggered alerts
- `alert_rules(id, scope[global|symbol], kind[volume_ratio|price_move|announcement_category], threshold, enabled)`. Defaults: volume >= 2x, price move >= +/-5%, categories: results, M&A, management_change, litigation, credit_rating downgrade.
- `alerts_scan.py` runs after each refresh. Idempotent via a unique key (rule, symbol, date/announcement_id). Writes `alert_events`.
- UI: Alerts tab = inbox with unread badge, a rules editor (sliders for thresholds, chips for categories, per-stock override), and a "dry run: what would have fired in the last 30 days" preview. That preview is great for the demo.
- Stretch: deliver via a Telegram bot or Web Push. In-app inbox is the minimum.
- Stretch (high value per effort): natural-language rule creation ("alert me if Suzlon volume is 3x") via the Ask bar. LLM outputs rule JSON, validated against the schema, and the UI confirms before saving.

### 6.7 Windowed digest
- Window chips: 24h / 3d / 7d / custom range. Default 24h.
- Header summary line (deterministic counts, plus one LLM sentence if cheap): "N filings, M must-see, K routine hidden".
- Toggle: group by priority (default) or by company.
- Filters: category chips, "my portfolio only".

## 7. Pull spec

### 7.1 The discipline
**Primary sources only:** annual reports and concall transcripts (plus screener's standardised financial tables, which are derived directly from filings; disclose this in the writeup). No news, no broker notes, no outside knowledge.
Enforce it in code, not only in the prompt:
- The LLM only ever sees retrieved chunks, each with an id like `[AR FY25 p.112]` or `[Concall Q2FY26 p.4]`.
- The system prompt says: answer only from the excerpts, cite chunk ids for every claim, say "not disclosed in sources" if absent.
- **Post-check:** parse citations; every claim must cite an id that was actually in the context. Drop or flag uncited claims. Show citations as tappable chips that open the source page excerpt.

### 7.2 Retrieval (zero embedding cost)
- Chunk by page/section (about 400-800 tokens, small overlap).
- BM25 (`rank_bm25`) per query over the company's chunks. Optional: local embeddings (fastembed) if BM25 is weak. No paid embeddings.
- Long annual reports (300+ pages): never send them whole. Locate sections by heading detection and keywords (Management Discussion, Segment information, Borrowings, Contingent liabilities, Related party, Auditor's report / Key Audit Matters, Cash flow), then retrieve top-k chunks per section.
- Default corpus per company: latest 1-2 annual reports and the last ~6 concall transcripts. Lazy-load on first request, cache forever.

### 7.3 Sections (build in this priority order; deterministic where possible)
Most "numbers" sections should be **computed from screener tables with no LLM** and charted. Use the LLM only for text. That is the cost story.

1. **Promised vs Delivered (hero, build first and best)**, see 7.4.
2. **Financial snapshot:** deterministic. Revenue, EBITDA/operating margin, PAT, EPS, ROCE/ROE, P/E and similar from screener. Compact tiles with sparklines.
3. **Business snapshot:** LLM from AR business overview and MD&A: what it does, segments, revenue mix (use segment note numbers if found). About 6 lines plus a mix bar.
4. **Bull vs bear:** strong model, fed the structured outputs of the other sections plus top cited excerpts. Three tight points each side, every point cited.
5. **Trajectory:** deterministic 5-10y CAGRs and margin trend, plus a 2-line cited LLM note on what is growing or compressing.
6. **Balance sheet:** deterministic: borrowings trend, D/E, interest coverage, receivable/inventory/payable days, cash conversion cycle. Optional cited note on contingent liabilities.
7. **Cash quality:** deterministic: CFO/PAT, FCF, cumulative CFO vs cumulative PAT. Flag persistent divergence.
8. **Guidance (list view):** reuse the guidance extraction from 7.4.

If time is short, ship 1, 2, 3, 4. Cut 5-8 to "coming next" in the writeup. Do not ship half-working sections.

### 7.4 Promised vs Delivered (the signature feature)
Pipeline:
1. **Extract (cheap model, per transcript):** schema `{metric, direction_or_target, low, high, unit, horizon (e.g. FY27 / next quarter / medium term), qualifier (hedged/firm), verbatim_quote (<=25 words), speaker, page, call_date}`. Only explicit forward-looking statements on headline numbers: revenue/volume growth, margins, capex, debt, ROCE, order book, etc. Skip vague optimism. Cache per transcript hash.
2. **Resolve (deterministic):** map each item (metric + horizon) to the actual from screener data for the matching period. Compute delivered vs target. Verdict by rules: **Delivered / Partly / Missed / Pending / Can't verify**. Pending means the horizon hasn't ended. Can't verify means no matching metric.
3. **Synthesize (strong model, tiny context):** the structured table only, in 3 lines: what is the pattern (e.g. consistently over-promises margins, under-promises growth), the biggest miss, the biggest beat. Cited.
4. UI: a vertical timeline of cards: "Promised" (quote and call date, tappable to the page) then "Delivered" (number, with mini bar vs target) then a verdict chip. A **credibility meter** summarising hit rate over the last four quarters.
Focus on the last four quarters, as the brief asks.

### 7.5 Pull UX
- Entry: the Ask bar (ticker or question) or the company page from any card.
- Dossier is a vertical stack of sections streaming in (SSE) with skeleton placeholders. Each section has a tappable cited-source drawer.
- Ask-follow-up box scoped to the company; answered with the same retrieval + cite-or-refuse pipeline.
- Show data freshness ("Based on AR FY25, concalls Q1FY26-Q2FY27").

## 8. LLM routing, caching, cost ledger

Provider-agnostic gateway (LiteLLM or an OpenAI-compatible client). Model names come from env vars so they can be swapped without code changes.

- **Cheap tier** (Gemini Flash-class free tier via AI Studio; alternates: Groq or OpenRouter free models): classification fallback, results-table extraction, guidance extraction, one-line summaries, intent routing.
- **Strong tier** (best model the user has access to, ideally also free-tier; otherwise the same cheap model with a longer, more careful prompt): bull/bear, pattern synthesis, ask-follow-ups.
- **Caching:** key = hash(model + prompt + input hash). Persist in SQLite. Extractions are cached per document forever.
- **Cost ledger:** `llm_ledger(ts, step, model, tokens_in, tokens_out, est_cost, cached)`. Expose `/api/costs` and render a small **"Cost to build this view"** footer or page (tokens, cache hit rate, model split). Efficiency earns bonus points, so make it visible.
- Structured output with pydantic validation, one retry with the validation error fed back, then fail gracefully in the UI (never a stack trace).
- Handle free-tier rate limits: queue with backoff, and pre-warm caches for demo companies (`scripts/warm_cache.py`).

## 9. Frontend spec

### 9.1 Design direction
Think "Bloomberg-meets-Things", not "SaaS dashboard template".
- Mobile-first at 390x844 (test there first). Safe-area insets, bottom tab bar (Briefing / Alerts / Research / Portfolio), thumb-reach actions, bottom sheets, swipe gestures where natural (swipe a card to dismiss or mark seen).
- Dark theme default (light supported via CSS variables, `prefers-color-scheme`).
- Type: one distinctive sans (e.g. IBM Plex Sans or Instrument Sans) plus a mono for numbers (JetBrains Mono or Geist Mono). `font-variant-numeric: tabular-nums` everywhere. Pick a deliberate palette with one accent colour. No purple gradients, no emoji as icons, no glassmorphism clutter, no generic equal-weight card grid.
- Priority is shown by a **left rail colour and card size**, not a pile of badges.
- Indian formatting: lakh/crore, Rs symbol, `en-IN` number format. Green/red deltas with sign and arrow (don't rely on colour alone).
- Density with hierarchy: big headline numbers, small muted metadata, generous touch targets (>=44px).
- Motion: purposeful only (sheet slide, streaming reveal, number count-up on the results card). Respect `prefers-reduced-motion`.
- Charts: tiny custom SVG sparklines and bars. Avoid heavy chart libs.
- States: skeletons, empty states with a useful action, readable error states.

### 9.2 Screens
1. **Briefing** (home): summary header, window chips, three lanes, group toggle. Cards per 6.3.
2. **Item sheet:** enriched detail, "why it ranked", source link, 3 suggested questions, ask box.
3. **Alerts:** inbox, rules editor, dry-run preview.
4. **Research:** search/ask, recent companies, the Dossier (7.3), Promised-vs-Delivered timeline.
5. **Portfolio:** compact list (price, day %, volume ratio flags, sparkline, unseen filings count), tap to company.
6. **Costs** (small, linked from settings): the cost ledger.

## 10. Demo robustness (judgment points)

- **The default 24h window will often be empty** (weekends, stale data). Include a "demo clock": a dataset backfilled about 30 days, a **"replay as of <date>"** selector, and auto-widen the window with a visible note when empty. Never show a blank home screen.
- **"As of" timestamps** on all data. If live refresh is blocked on the host, serve the snapshot and say so.
- Prices are EOD only, so the UI says "EOD" and never implies live.
- Provide a `/health` page and a `scripts/refresh.py` that rebuilds the snapshot locally.

## 11. Phased execution (time-boxed; stop after each phase)

### Phase 0: Spikes (about 1h)
Write throwaway scripts in `scripts/spike_*.py` and save raw outputs to `data/spikes/`. Report what works.
- yfinance for 3 tickers (.NS) incl. 20d volume average.
- NSE announcements for 3 tickers: does it work from this machine? From a cloud host (note the risk)? Try `requests`, then `curl_cffi`.
- NSE financial-results structured endpoint: exists? returns Q/PrevQ/YoY? XBRL link?
- BSE announcements for 1 ticker.
- screener.in page for 3 tickers: parse quarterly table and the Documents section (AR links, concall links). Download 1 AR and 1 concall PDF, extract text, check quality.
- One cheap-LLM call returning pydantic-validated JSON via the gateway skeleton.
- **Output:** a table of what works / what's blocked / what the fallback is, and any needed change to the architecture. Update DECISIONS.md.
- **Acceptance:** every source has a confirmed working path or a stated fallback.

### Phase 1: Ingestion and storage (about 2.5h)
- DB schema, `portfolio.json`, prices ingest, announcements ingest (30-day backfill), screener tables, doc list.
- `scripts/refresh.py` runs everything idempotently with raw caching.
- **Acceptance:** DB holds 18 stocks of prices, 30 days of announcements, screener tables. Re-running costs ~0 network.

### Phase 2: Push backend (about 2h)
- Classifier, priority scoring with breakdown, anomaly join, digest API (window param), alerts rules/scan/events API.
- **Acceptance:** `/api/digest?window=7d` returns tiered items with score breakdowns. A volume 2x rule fires on real historical data.

### Phase 3: Results + management-change enrichment (about 2h)
- Results extraction with validation (prefer structured NSE data), derived EBITDA, QoQ/YoY. People extraction and enrichment with source links.
- **Acceptance:** at least 5 real results filings produce correct, validated cards (spot-check 2 against the filing by hand and report). One real management change has a sourced bio, or an honest "couldn't verify".

### Phase 4: Frontend, push side (about 3h)
- Scaffold, design tokens, Briefing screen, cards, item sheet, window chips, Alerts screen, Portfolio list, PWA manifest.
- Serve the built app from FastAPI.
- **Acceptance:** works at 390px on a real phone via LAN. Passes the "10 seconds to know what matters" test.
- **Deploy checkpoint:** deploy the push side now, so a working link exists early.

### Phase 5: Pull backend (about 3.5h)
- Retrieval, citation enforcement, guidance extraction, Promised vs Delivered resolver, deterministic snapshot/balance/cash sections, business snapshot, bull/bear, SSE endpoint.
- **Acceptance:** run on 2 portfolio tickers plus 1 unseen ticker. Show the Promised-vs-Delivered table for one, with citations that resolve to real pages.

### Phase 6: Frontend, pull side + Ask bar (about 2h)
- Research screen, streaming dossier, timeline, citation drawer, follow-up ask, global Ask bar with intent routing. NL alert creation if time allows.
- **Acceptance:** end-to-end demo on a phone.

### Phase 7: Polish, deploy, document (about 2h)
- Cost page, empty/error states, demo-clock replay, warm caches, final deploy, README (run instructions, architecture diagram in Mermaid, screenshots), finalise DECISIONS.md, clean `.env.example`.
- Draft the covering-email writeup from DECISIONS.md (see 13).
- **Acceptance:** a fresh clone runs with documented steps. The deployed link works on a phone with no setup.

## 12. If time runs out: cut order (cut from the bottom)

Keep: Briefing + priority, results card, management-change card, alert rules and dry-run, Promised-vs-Delivered, financial snapshot, business snapshot, bull/bear, cost ledger.
Cut first: voice, Telegram/Web Push, NL alert creation, trajectory/balance-sheet/cash-quality LLM notes, per-stock alert overrides, light theme.
Never cut: caching, snapshot fallback, honest "unverified"/"not disclosed" states.

## 13. Covering-email writeup (draft from DECISIONS.md)
Structure: (1) what I built and the interaction POV; (2) key decisions and trade-offs (snapshot-first architecture, cheap-vs-strong routing, deterministic numbers vs LLM text, citation enforcement, derived EBITDA, ranking with price/volume fusion); (3) what I deliberately left out; (4) cost and efficiency numbers from the ledger; (5) where I'd take it next; (6) links: app, repo, AI build history.
Be honest about limitations (unofficial NSE/BSE endpoints, screener dependency, banks' results format).

## 14. Build history (graded)
Keep conversations exportable. Use the tool's export/share feature at the end of each phase (e.g. `/share` or `/export` where available) and save links or files in `docs/build-history/`. The git history should show one commit per phase minimum with clear messages.
