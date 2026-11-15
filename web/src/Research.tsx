// Research.tsx — deterministic company dossier from screener standardised tables.
// Numbers arrive computed (no LLM, no network in snapshot mode); missing data
// renders "not available", never a guess.
import { ReactNode, useEffect, useState } from "react";
import { api, AskHit, Dossier, Narrative, NarrativeLine, Promised, SeriesPt } from "./api";
import PromisedView from "./Promised";
import { Chip, Empty, ErrorBox, Skeleton, Sparkline } from "./ui";

const cr = (v: number | null | undefined, digits = 0) =>
  v == null ? "not available" : `₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(v)} cr`;
const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? "not available" : `${new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(v)}%`;
const x = (v: number | null | undefined, digits = 2) =>
  v == null ? "not available" : `${new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(v)}x`;
const shortFY = (p: string | null) => (p ? `FY${p.slice(-2)}` : "");

// Tappable one-line plain-English definition. title-attr doesn't work on touch.
function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        aria-label="What is this?"
        onClick={() => setOpen(!open)}
        style={{
          width: 22, height: 22, borderRadius: 11, border: "1px solid var(--line)",
          background: "none", color: "var(--muted)", fontSize: 12, lineHeight: 1,
          cursor: "pointer", flexShrink: 0,
        }}
      >
        ?
      </button>
      {open && <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6, width: "100%" }}>{text}</div>}
    </>
  );
}

function Section({ title, tip, children }: { title: string; tip: string; children: ReactNode }) {
  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <h2 style={{ fontSize: 13, fontWeight: 700, margin: 0, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--text)" }}>
          {title}
        </h2>
        <InfoTip text={tip} />
      </div>
      {children}
    </div>
  );
}

// Hand-rolled SVG bar chart. Negatives render below the baseline in red.
function Bars({ data, h = 72, fmt }: { data: SeriesPt[]; h?: number; fmt: (v: number | null) => string }) {
  const pts = data.filter((d) => d.v != null) as { p: string; v: number }[];
  if (pts.length < 1) return <div style={{ color: "var(--muted)", fontSize: 13 }}>not available</div>;
  const w = 340;
  const max = Math.max(0, ...pts.map((d) => d.v));
  const min = Math.min(0, ...pts.map((d) => d.v));
  const span = max - min || 1;
  const zeroY = h - 4 - (max / span) * (h - 10);
  const bw = Math.min(28, (w - 8) / pts.length - 6);
  const step = (w - 8) / pts.length;
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h + 14}`} style={{ width: "100%", height: "auto" }} aria-hidden="true">
        <line x1="0" x2={w} y1={zeroY} y2={zeroY} stroke="var(--line)" strokeWidth="1" />
        {pts.map((d, i) => {
          const bh = Math.max(1, (Math.abs(d.v) / span) * (h - 10));
          const bx = 4 + i * step + (step - bw) / 2;
          const up = d.v >= 0;
          return (
            <rect key={d.p} x={bx} y={up ? zeroY - bh : zeroY} width={bw} height={bh}
              rx="2" fill={up ? "var(--accent)" : "var(--down)"} opacity={i === pts.length - 1 ? 1 : 0.55} />
          );
        })}
        <text x={4} y={h + 12} fontSize="9" fill="var(--muted)">{shortFY(pts[0].p)}</text>
        <text x={w - 4} y={h + 12} fontSize="9" fill="var(--muted)" textAnchor="end">{shortFY(pts[pts.length - 1].p)}</text>
        <text
          x={w - 4}
          y={pts[pts.length - 1].v >= 0 ? zeroY - (Math.abs(pts[pts.length - 1].v) / span) * (h - 10) - 3 : zeroY - 3}
          fontSize="9" fill="var(--text)" textAnchor="end" className="num">{fmt(pts[pts.length - 1].v)}</text>
      </svg>
    </div>
  );
}

function Cited({ lines }: { lines: NarrativeLine[] }) {
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {lines.map((line) => (
        <p key={line.chunk_id + line.text.slice(0, 24)} style={{ margin: 0, fontSize: 13, lineHeight: 1.45 }}>
          {line.text}{" "}
          <span className="chip" style={{ color: "var(--info)" }}>{line.chunk_id === "tables" ? "screener tables" : line.chunk_id}</span>
        </p>
      ))}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div style={{ padding: "8px 10px", border: "1px solid var(--line)", borderRadius: 8, minWidth: 0 }}>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{label}</div>
      <div className="num" style={{ fontSize: 15, fontWeight: 600, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: "var(--muted)" }}>{sub}</div>}
    </div>
  );
}

const TIPS: Record<string, string> = {
  snapshot: "The latest full picture of the business. Operating margin (EBITDA margin) is the share of revenue left after running costs, before interest and tax. ROCE = profit before interest and tax as a share of all capital used; ROE = profit as a share of shareholders' money.",
  trajectory: "How the business has grown. CAGR is the smoothed yearly growth rate over the period shown; the margin trend shows whether profitability is expanding or compressing as revenue grows.",
  balance: "How the company is funded and how quickly it collects and pays. Debt/equity compares borrowings with shareholders' funds. Debtor/inventory/payable days are how long cash is stuck with customers, in stock, and how long it takes to pay suppliers.",
  cash: "Cash conversion = operating cash flow as a share of reported net profit. Persistent gaps mean accounting profit isn't turning into actual cash — a classic quality warning.",
};

export default function Research() {
  const [symbols, setSymbols] = useState<{ portfolio: string[]; more: string[] }>({ portfolio: [], more: [] });
  const [symbol, setSymbol] = useState<string>("");
  const [d, setD] = useState<Dossier | null>(null);
  const [promised, setPromised] = useState<Promised | null>(null);
  const [narrative, setNarrative] = useState<Narrative | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [askQ, setAskQ] = useState("");
  const [askHits, setAskHits] = useState<AskHit[] | null>(null);
  const [askAnswer, setAskAnswer] = useState<{ text: string; chunk_id: string }[]>([]);
  const [askNote, setAskNote] = useState("");
  const [asking, setAsking] = useState(false);

  useEffect(() => {
    api.researchSymbols().then((r) => {
      setSymbols(r);
      const withCalls = new Set(r.with_calls || []);
      const preferred = (r.with_calls || []).find((s) => r.portfolio.includes(s)) || r.with_calls?.[0];
      setSymbol((s) => s || preferred || r.portfolio.find((x) => withCalls.has(x)) || r.portfolio[0] || r.more[0] || "");
    }).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    setPromised(null);
    setNarrative(null);
    setAskHits(null);
    setAskAnswer([]);
    setAskNote("");
    api.dossier(symbol)
      .then(setD)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
    api.promised(symbol).then(setPromised).catch(() => setPromised(null));
    api.narrative(symbol).then(setNarrative).catch(() => setNarrative(null));
  }, [symbol]);

  const submitAsk = () => {
    const q = askQ.trim();
    if (q.length < 3 || !symbol) return;
    setAsking(true);
    api.ask(symbol, q)
      .then((r) => { setAskHits(r.hits); setAskAnswer(r.answer || []); setAskNote(r.note); })
      .catch((e) => { setAskHits([]); setAskNote(String(e)); })
      .finally(() => setAsking(false));
  };

  const chipRow = (list: string[]) =>
    list.map((s) => (
      <button key={s} className={`chip${s === symbol ? " accent" : ""}`} onClick={() => setSymbol(s)}
        style={{ flexShrink: 0, cursor: "pointer" }}>
        {s}
      </button>
    ));

  return (
    <div>
      <h1 style={{ fontSize: 20, margin: "0 0 4px", fontWeight: 700 }}>Research</h1>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10 }}>
        Filings first. Numbers are computed from screener's tables. Promises are quotes from the concall, checked against those tables.
      </div>

      <div style={{ display: "flex", gap: 6, overflowX: "auto", paddingBottom: 6, marginBottom: 12 }}>
        {chipRow(symbols.portfolio)}
        {symbols.more.length > 0 && <span style={{ color: "var(--muted)", alignSelf: "center", fontSize: 12, flexShrink: 0 }}>more:</span>}
        {chipRow(symbols.more)}
      </div>

      {error && <ErrorBox msg={error} retry={() => setSymbol(symbol)} />}
      {loading && <Skeleton rows={4} />}

      {!loading && d && !d.available && (
        <Empty title={`${d.symbol}: not available`} hint={d.note || "no screener data ingested for this ticker"} />
      )}

      {!loading && d && d.available && d.snapshot && d.trajectory && d.balance && d.cash && (
        <div style={{ display: "grid", gap: 12 }}>
          <div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 17, fontWeight: 700 }}>{d.name || d.symbol}</span>
              <Chip accent={!!d.is_bank}>{d.symbol}</Chip>
              {d.is_bank && <Chip>bank/NBFC</Chip>}
            </div>
          </div>

          {promised ? <PromisedView data={promised} /> : <Skeleton rows={2} />}

          <Section title="Financial snapshot" tip={TIPS.snapshot}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {d.snapshot.map((t) => (
                <div key={t.label} style={{ border: "1px solid var(--line)", borderRadius: 10, padding: "10px 12px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
                    <span style={{ fontSize: 11, color: "var(--muted)" }}>
                      {t.label}{t.note ? " *" : ""}
                    </span>
                    <span className="num" style={{ fontSize: 10, color: "var(--muted)" }}>{t.period == "TTM" ? "TTM" : shortFY(t.period)}</span>
                  </div>
                  <div className="num" style={{ fontSize: 19, fontWeight: 700, marginTop: 2 }}>
                    {t.value == null ? "—" : `${t.pct ? "" : t.unit == "cr" ? "₹" : ""}${new Intl.NumberFormat("en-IN", { maximumFractionDigits: t.pct ? 0 : t.unit == "Rs" ? 2 : 0 }).format(t.value)}${t.pct ? "%" : t.unit == "Rs" ? "" : " cr"}`}
                  </div>
                  <div style={{ marginTop: 4 }}>
                    <Sparkline points={t.spark} w={120} h={24} />
                  </div>
                  {t.note && <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 3 }}>* {t.note}</div>}
                </div>
              ))}
            </div>
          </Section>

          {narrative && narrative.status === "ready" && narrative.business.length > 0 && (
            <Section title="Business" tip="What the company does, taken from its annual report or concall. Every sentence cites the page it came from.">
              <Cited lines={narrative.business} />
            </Section>
          )}

          {narrative && narrative.status === "ready" && (narrative.bull.length > 0 || narrative.bear.length > 0) && (
            <Section title="Bull vs bear" tip="Points on each side are facts from the filings or the standardised tables.">
              <div style={{ display: "grid", gap: 12 }}>
                {narrative.bull.length > 0 && (
                  <div>
                    <div style={{ fontSize: 11, color: "var(--up)", fontWeight: 700, marginBottom: 6 }}>BULL</div>
                    <Cited lines={narrative.bull} />
                  </div>
                )}
                {narrative.bear.length > 0 && (
                  <div>
                    <div style={{ fontSize: 11, color: "var(--down)", fontWeight: 700, marginBottom: 6 }}>BEAR</div>
                    <Cited lines={narrative.bear} />
                  </div>
                )}
              </div>
            </Section>
          )}

          <Section title="Trajectory" tip={TIPS.trajectory}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
                  <span>Sales / yr</span>
                  <span className="num">CAGR {d.trajectory.sales_cagr == null ? "n/a" : `${d.trajectory.sales_cagr > 0 ? "+" : ""}${d.trajectory.sales_cagr}%`}</span>
                </div>
                <Bars data={d.trajectory.sales} fmt={(v) => cr(v)} />
              </div>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
                  <span>Net profit / yr</span>
                  <span className="num">CAGR {d.trajectory.profit_cagr == null ? "n/a" : `${d.trajectory.profit_cagr > 0 ? "+" : ""}${d.trajectory.profit_cagr}%`}</span>
                </div>
                <Bars data={d.trajectory.profit} fmt={(v) => cr(v)} />
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
              <span style={{ fontSize: 11, color: "var(--muted)", flexShrink: 0 }}>Operating margin trend</span>
              <Sparkline points={d.trajectory.margin.map((m) => m.v).filter((v): v is number => v != null)} w={150} h={26} />
              <span className="num" style={{ fontSize: 13, fontWeight: 600 }}>
                {pct(d.trajectory.margin.length ? d.trajectory.margin[d.trajectory.margin.length - 1].v : null, 0)}
              </span>
            </div>
          </Section>

          <Section title="Balance sheet" tip={TIPS.balance}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>Borrowings / yr</div>
                <Bars data={d.balance.borrowings} fmt={(v) => cr(v)} h={60} />
              </div>
              <div style={{ display: "grid", gap: 8, alignContent: "start" }}>
                <Stat label="Debt / Equity" value={x(d.balance.de)} />
                <Stat label="Debtor days" value={d.balance.days.debtor.value == null ? "not available" : `${d.balance.days.debtor.value} d`} />
                <Stat label="Inventory days" value={d.balance.days.inventory.value == null ? "not available" : `${d.balance.days.inventory.value} d`} />
                <Stat label="Payable days" value={d.balance.days.payable.value == null ? "not available" : `${d.balance.days.payable.value} d`} />
              </div>
            </div>
            {d.balance.note && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>{d.balance.note}</div>}
          </Section>

          <Section title="Cash quality" tip={TIPS.cash}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>Cash from operations / yr</div>
                <Bars data={d.cash.years.map((y) => ({ p: y.p, v: y.cfo }))} fmt={(v) => cr(v)} h={60} />
              </div>
              <div>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>Net profit / yr</div>
                <Bars data={d.cash.years.map((y) => ({ p: y.p, v: y.profit }))} fmt={(v) => cr(v)} h={60} />
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 10 }}>
              <Stat label="Cum. CFO" value={cr(d.cash.cum_cfo)} />
              <Stat label="Cum. profit" value={cr(d.cash.cum_profit)} />
              <Stat label="Conversion" value={x(d.cash.cum_ratio)} sub={`${d.cash.years.length} yrs`} />
            </div>
            {d.cash.flag && (
              <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 8, border: "1px solid color-mix(in srgb, var(--down) 45%, transparent)", color: "var(--down)", fontSize: 12 }}>
                ▼ Cumulative operating cash flow is below 70% of cumulative reported profit over this window.
              </div>
            )}
            {d.cash.note && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>{d.cash.note}</div>}
          </Section>

          <Section title="Ask the filings" tip="The answer may only use this company's annual report and concall pages. Anything that cannot cite one of those pages is dropped.">
            <form onSubmit={(e) => { e.preventDefault(); submitAsk(); }} style={{ display: "flex", gap: 8 }}>
              <input
                value={askQ}
                onChange={(e) => setAskQ(e.target.value)}
                placeholder={`What did ${symbol} say about margins?`}
                aria-label="Ask the filings"
                style={{
                  flex: 1, minWidth: 0, background: "var(--surface2)", color: "var(--text)",
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px", fontSize: 14,
                }}
              />
              <button className="btn" type="submit" disabled={asking || askQ.trim().length < 3} style={{ minWidth: 64 }}>
                {asking ? "…" : "Ask"}
              </button>
            </form>
            {askNote && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>{askNote}</div>}
            {askAnswer.length > 0 && (
              <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                {askAnswer.map((line) => (
                  <p key={line.chunk_id + line.text.slice(0, 24)} style={{ margin: 0, fontSize: 14, lineHeight: 1.45 }}>
                    {line.text}{" "}
                    <span className="chip" style={{ color: "var(--info)" }}>{line.chunk_id}</span>
                  </p>
                ))}
              </div>
            )}
            {askHits && askHits.length === 0 && <div style={{ fontSize: 13, color: "var(--muted)", marginTop: 8 }}>No matching excerpt.</div>}
            <div style={{ display: "grid", gap: 8, marginTop: askHits && askHits.length ? 10 : 0 }}>
              {askHits?.map((h) => (
                <div key={h.chunk_id} style={{ borderTop: "1px solid var(--line)", paddingTop: 8 }}>
                  <div style={{ fontSize: 11, color: "var(--info)", marginBottom: 4 }}>
                    {h.doc_kind === "CONCALL" ? "Concall" : "Annual report"} {h.period} · {h.chunk_id}{h.page ? ` · p.${h.page}` : ""}
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.45 }}>{h.text}</div>
                </div>
              ))}
            </div>
          </Section>

          <div style={{ paddingBottom: 4 }}>
            <Chip>source: screener standardised tables · as of {d.as_of || "unknown date"}</Chip>
          </div>
        </div>
      )}
    </div>
  );
}
