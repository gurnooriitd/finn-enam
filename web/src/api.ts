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

export interface DigestItem {
  id: string;
  symbol: string;
  ts: string;
  subject: string;
  voice_summary: string | null;
  attachment_url: string;
  category: string;
  score: number;
  tier: string;
  score_breakdown: ScoreBreakdown;
  price: number | null;
  volume: number | null;
}

export interface Digest {
  window: string;
  cutoff: string;
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
}

export interface Holding {
  symbol: string;
  name: string;
  last_close: number | null;
  day_pct: number | null;
  volume_ratio: number | null;
  spark: number[];
  unseen_filings: number;
}

export interface PortfolioResp {
  synthetic: boolean;
  note: string;
  holdings: Holding[];
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

export const api = {
  digest: (window: string) => getJSON<Digest>(`/api/digest?window=${window}`),
  rules: () => getJSON<Rule[]>("/api/alerts/rules"),
  events: (limit = 100) => getJSON<AlertEvent[]>(`/api/alerts/events?limit=${limit}`),
  dryrun: () => getJSON<{ total_would_fire: number; events: AlertEvent[] }>("/api/alerts/dryrun"),
  portfolio: () => getJSON<PortfolioResp>("/api/portfolio"),
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

export const inr = (v: number | null | undefined) =>
  v == null
    ? "—"
    : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(v);

export const num = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(v);

export const DELTA_TIER_STYLES = "var(--up)";
