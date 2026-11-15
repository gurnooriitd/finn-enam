Subject: IIT Delhi – AI Engineer - 2027

FINN is a phone briefing for an Indian-equity investor. The home screen is not a dashboard. It says how many filings arrived and how many of them matter, then stacks Must-see, Worth a look, and Routine. A card opens into the filing, the reason it ranked (including the price and volume move on the filing day), and the source PDF. Alerts are the same idea with thresholds the investor sets, plus a dry run of what would have fired. Research is the other half: ask a company what it promised, and check that against the numbers.

Live app: https://finn-enam-st9b.onrender.com/
Repo: https://github.com/gurnooriitd/finn-enam

Decisions that shaped it

The hosted app serves a snapshot. NSE and screener block datacenter IPs, so the container does not fetch live and does not call a model. Prices, filings, screener tables, transcript chunks, guidance quotes, and the cited business notes are already in the SQLite file. Refresh locally, then redeploy.

Numbers are computed. Classification is rules on the filing subject. Results cards and the financial dossier come from screener’s standardised tables. EBITDA on a results card is derived and labelled when it is derived. A figure that fails a check is shown as unverified, not hidden.

The model is used offline, and then checked. Guidance extraction keeps a sentence only if the quote is actually in the cited transcript page. The target number is parsed from that quote, not taken from the model. The verdict (delivered, partly, missed, pending, can’t verify) is arithmetic against one screener column. Beating a growth range counts as delivered. A margin above the guided band stays “partly”, because screener’s operating margin is often a different line, and the card says so. Constant-currency guidance is shown and not scored. Business and bull/bear lines are dropped if they do not cite a chunk that was in the prompt. On the hosted app, Ask shows the matching annual-report and concall pages. The same route can write a short answer when an API key is present, and any sentence that does not cite one of those pages is dropped. The hosted container has no key, so it does not make that call.

What I left out

Voice, Telegram, and commentary on the cash and balance-sheet charts. Those charts are already computed. A model paragraph on top of them would have been the expensive, less reliable part.

Cost

Hosted runtime is zero model tokens. Extraction cost is in the ledger at /api/costs (cheap model for guidance quotes, strong model for the cited business note). Repeating a run hits the cache.

Where next

A follow-up answer that is still forced to cite, once the host can call a model. Constant-currency actuals for Infosys, so that guidance can be scored instead of parked. Live refresh from a residential IP, with the snapshot as the fallback the app already has.

Limitations

NSE’s announcement API is unofficial. BSE’s API was blocked, so filings are NSE plus the PDFs screener links. Banks do not get an EBITDA row. Portfolio weights are mock and marked synthetic. Prices are end of day.
