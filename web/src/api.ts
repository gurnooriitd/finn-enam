// api.ts — typed client for the real FINN backend responses.

export interface ScoreBreakdown {
  category_base?: number;
  portfolio_weight?: number;
  weight_mult?: number;
  keyword_boost?: number;
  keyword_hits?: string[];
  mgmt_senior_boost?: number;
  rating_downgrade_boost?: number;
  vol_ratio?: number | null;
  price_move_pct?: number | null;
  anomaly_boost?: number;
  anomaly_note?: string;
  final_score?: number;
  [k: string]: unknown;
}

export interface PriceCtx {
  day_pct: number | null;
  vol_ratio: number | null;
  price_date: string | null;
}

export interface DigestChild {
  id: string;
  ts: string;
  ts_iso: string | null;
  subject: string;
  voice_summary: string | null;
  attachment_url: string;
  score: number | null;
  price_ctx: PriceCtx;
}

export interface ResultsRow {
  metric: string;
  value: number | null;
  qoq_pct: number | null;
  yoy_pct: number | null;
  qoq_abs: number | null;
  yoy_abs: number | null;
}

export interface ResultsSummary {
  quarter: string;
  filing_quarter: string | null;
  basis: string;
  unit: string;
  rows: ResultsRow[];
  ebitda_margin: number | null;
  pat_margin: number | null;
  ebitda_margin_yoy?: number | null;
  pat_margin_yoy?: number | null;
  takeaway?: string;
  note: string;
  available: boolean;
}

export interface MgmtChange {
  person: string;
  role: string | null;
  action: string;
  effective_date: string | null;
  reason?: string | null;
  predecessor?: string | null;
  bio?: string | null;
  confidence?: string | null;
  sources?: { title: string | null; url: string; snippet: string | null }[] | null;
}

export interface DigestItem {
  id: string;
  symbol: string;
  ts: string;
  ts_iso: string | null;
  subject: string;
  voice_summary: string | null;
  headline?: string;
  mentions_results?: boolean;
  results_summary?: ResultsSummary | null;
  mgmt_change?: MgmtChange | null;
  attachment_url: string;
  category: string;
  score: number;
  tier: string;
  score_breakdown: ScoreBreakdown;
  child_count: number;
  price_ctx: PriceCtx;
  children: DigestChild[];
}

export interface Digest {
  window: string;
  cutoff: string;
  data_as_of: string | null;
  total: number;
  summary: { must_see: number; worth_a_look: number; routine: number };
  must_see: DigestItem[];
  worth_a_look: DigestItem[];
  routine: { count: number; items: DigestItem[] };
}

export interface Rule {
  id: number;
  scope: string;
  symbol: string | null;
  kind: "volume_ratio" | "price_move" | "announcement_category";
  threshold: number | null;
  categories: string | null;
  enabled: number;
}

export interface AlertEvent {
  id: number;
  rule_id: number;
  symbol: string;
  date: string;
  announcement_id: string;
  kind: string;
  value: number;
  threshold: number;
  message: string;
  fired_at: string;
  company?: string;
  reason?: string;
  summary?: string;
  attachment_url?: string;
}

export interface Holding {
  symbol: string;
  name: string;
  last_close: number | null;
  day_pct: number | null;
  volume_ratio: number | null;
  weight?: number | null;
  spark: number[];
  unseen_filings: number;
}

export interface PortfolioResp {
  synthetic: boolean;
  note: string;
  data_as_of?: string | null;
  holdings: Holding[];
}

export interface ResearchSymbols {
  portfolio: string[];
  more: string[];
}

export interface DossierTile {
  label: string;
  value: number | null;
  period: string | null;
  unit: string;
  pct?: boolean;
  spark: number[];
  spark_labels: string[];
  note?: string;
}

export interface SeriesPt {
  p: string;
  v: number | null;
}

export interface Dossier {
  symbol: string;
  name?: string;
  available?: boolean;
  is_bank?: boolean;
  as_of?: string;
  unit?: string;
  snapshot?: DossierTile[];
  trajectory?: {
    sales: SeriesPt[];
    profit: SeriesPt[];
    margin: SeriesPt[];
    sales_cagr: number | null;
    profit_cagr: number | null;
  };
  balance?: {
    borrowings: SeriesPt[];
    de: number | null;
    days: { debtor: { value: number | null; period: string | null }; inventory: { value: number | null; period: string | null }; payable: { value: number | null; period: string | null } };
    note?: string;
  };
  cash?: {
    years: { p: string; cfo: number | null; profit: number | null; ratio: number | null }[];
    cum_cfo: number | null;
    cum_profit: number | null;
    cum_ratio: number | null;
    flag: boolean;
    note?: string;
  };
  note?: string;
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

export interface Meta {
  data_as_of: string | null;
  mode: string;
}

export const api = {
  digest: (window: string) => getJSON<Digest>(`/api/digest?window=${window}`),
  meta: () => getJSON<Meta>("/api/meta"),
  rules: () => getJSON<Rule[]>("/api/alerts/rules"),
  events: (limit = 100) => getJSON<AlertEvent[]>(`/api/alerts/events?limit=${limit}`),
  dryrun: () => getJSON<{ total_would_fire: number; events: AlertEvent[] }>("/api/alerts/dryrun"),
  portfolio: () => getJSON<PortfolioResp>("/api/portfolio"),
  researchSymbols: () => getJSON<ResearchSymbols>("/api/research"),
  dossier: (symbol: string) => getJSON<Dossier>(`/api/research/${symbol}`),
  updateRule: (id: number, body: Partial<Rule>) =>
    fetch(`/api/alerts/rules/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scope: body.scope ?? "global",
        symbol: body.symbol ?? null,
        kind: body.kind!,
        threshold: body.threshold ?? null,
        categories: body.categories ?? null,
        enabled: !!(body.enabled ?? 1),
      }),
    }).then((r) => r.json()),
};

// ---- formatting helpers ----
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// NSE ts looks like "31-Oct-2026 17:34:03"
export function parseTs(ts: string): Date | null {
  if (!ts) return null;
  const m = ts.match(/^(\d{1,2})-([A-Za-z]{3})-(\d{4})\s+(\d{2}):(\d{2})/);
  if (!m) return null;
  const mo = MONTHS.indexOf(m[2].slice(0, 3).replace(/^(\w)/, (c) => c.toUpperCase()));
  if (mo < 0) return null;
  return new Date(+m[3], mo, +m[1], +m[4], +m[5]);
}

export function fmtTs(ts: string): string {
  const d = parseTs(ts);
  if (!d) return ts || "";
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${hh}:${mm}`;
}

// Full date from an ISO date string: "2026-10-31" -> "31 Oct 2026"
export function fmtDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso.slice(0, 10) + "T00:00:00");
  return isNaN(d.getTime()) ? iso : `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

export const inr = (v: number | null | undefined) =>
  v == null
    ? "—"
    : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(v);

export const num = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(v);

export const DELTA_TIER_STYLES = "var(--up)";
