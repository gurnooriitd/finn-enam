import { useCallback, useEffect, useState } from "react";
import { api, AlertEvent, Rule } from "./api";
import { Chip, Empty, ErrorBox, Skeleton } from "./ui";

const CATS = ["results", "M&A", "management_change", "litigation", "credit_rating", "fund_raise", "dividend", "pledging", "insider_trading"];

type View = "inbox" | "rules" | "dryrun";

export default function Alerts({ onSeen, onOpenStock }: { onSeen: () => void; onOpenStock: (s: string) => void }) {
  const [view, setView] = useState<View>("inbox");
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [dry, setDry] = useState<{ total_would_fire: number; events: AlertEvent[] } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const [ev, rl] = await Promise.all([api.events(200), api.rules()]);
      setEvents(ev);
      setRules(rl);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const seenIds = new Set<string>(JSON.parse(localStorage.getItem("finn.seenEvents") || "[]"));

  const saveRule = async (rule: Rule, patch: Partial<Rule>) => {
    const next = { ...rule, ...patch };
    setRules((rs) => rs.map((r) => (r.id === rule.id ? next : r)));
    await api.updateRule(rule.id, next);
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <h1 style={{ fontSize: 20, margin: 0, fontWeight: 700 }}>Alerts</h1>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          {(["inbox", "rules", "dryrun"] as View[]).map((v) => (
            <button key={v} className="chip" style={view === v ? { color: "var(--text)", borderColor: "var(--muted)" } : undefined} onClick={() => setView(v)}>
              {v === "dryrun" ? "dry run" : v}
            </button>
          ))}
        </div>
      </div>

      {loading && <Skeleton rows={3} />}
      {err && <ErrorBox msg={err} retry={load} />}

      {!loading && !err && view === "inbox" && (
        <>
          {events.length === 0 && <Empty title="No alerts fired yet" hint="Rules run after every data refresh." />}
          {events.length > 0 && (
            <>
              <button className="btn" style={{ marginBottom: 10 }} onClick={onSeen}>
                Mark all read
              </button>
              {events.map((e) => {
                const unread = !seenIds.has(String(e.id));
                return (
                  <button
                    key={e.id}
                    className="card rail"
                    style={{
                      padding: "10px 12px",
                      marginBottom: 6,
                      display: "flex",
                      gap: 8,
                      alignItems: "baseline",
                      borderLeftColor: unread ? "var(--accent)" : "var(--line)",
                    }}
                    onClick={() => onOpenStock(e.symbol)}
                  >
                    <span className="num" style={{ fontWeight: unread ? 700 : 500, minWidth: 86, textAlign: "left", fontSize: 13 }}>
                      {e.symbol}
                    </span>
                    <span style={{ fontSize: 13, flex: 1, textAlign: "left" }}>{e.message}</span>
                    <span className="num" style={{ color: "var(--muted)", fontSize: 11.5 }}>{e.date}</span>
                  </button>
                );
              })}
            </>
          )}
        </>
      )}

      {!loading && !err && view === "rules" && (
        <>
          {rules.map((r) => (
            <div key={r.id} className="card" style={{ padding: 14, marginBottom: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <span style={{ fontWeight: 600 }}>
                  {r.kind === "volume_ratio" ? "Volume spike" : r.kind === "price_move" ? "Price move" : "Announcement categories"}
                </span>
                {r.symbol && <Chip>{r.symbol}</Chip>}
                <label style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center", fontSize: 13, color: "var(--muted)", minHeight: 44 }}>
                  <input type="checkbox" checked={!!r.enabled} onChange={(e) => saveRule(r, { enabled: e.target.checked ? 1 : 0 })} />
                  on
                </label>
              </div>

              {(r.kind === "volume_ratio" || r.kind === "price_move") && (
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <input
                    className="input num"
                    type="number"
                    step="0.5"
                    min="0"
                    defaultValue={r.threshold ?? undefined}
                    key={`${r.id}-${r.threshold}`}
                    style={{ width: 110 }}
                    onBlur={(e) => {
                      const v = parseFloat(e.target.value);
                      if (!isNaN(v) && v !== r.threshold) saveRule(r, { threshold: v });
                    }}
                  />
                  <span style={{ color: "var(--muted)", fontSize: 13 }}>
                    {r.kind === "volume_ratio" ? "× 20-day average volume" : "% day move (±)"}
                  </span>
                </div>
              )}

              {r.kind === "announcement_category" && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {CATS.map((c) => {
                    const cats = (r.categories || "").split(",").filter(Boolean);
                    const on = cats.includes(c);
                    return (
                      <button
                        key={c}
                        className="chip"
                        style={on ? { color: "var(--text)", borderColor: "var(--accent)" } : undefined}
                        onClick={() => saveRule(r, { categories: (on ? cats.filter((x) => x !== c) : [...cats, c]).join(",") })}
                      >
                        {c}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </>
      )}

      {!loading && !err && view === "dryrun" && (
        <>
          {!dry && (
            <button
              className="btn primary"
              onClick={() => api.dryrun().then(setDry).catch((e) => setErr(String(e)))}
            >
              Preview last 30 days
            </button>
          )}
          {dry && (
            <>
              <div className="card rail" style={{ padding: 12, marginBottom: 10, borderLeftColor: "var(--accent)" }}>
                <span className="num" style={{ fontWeight: 700 }}>{dry.total_would_fire}</span> events would have fired in the
                last 30 days with current rules.
              </div>
              {dry.events.slice(0, 30).map((e) => (
                <div key={e.id} className="card" style={{ padding: "9px 12px", marginBottom: 6, fontSize: 13 }}>
                  {e.message}
                  <span className="num" style={{ color: "var(--muted)", fontSize: 11.5, marginLeft: 8 }}>
                    {e.kind} ≥ {e.threshold}
                  </span>
                </div>
              ))}
              {dry.events.length > 30 && (
                <div style={{ color: "var(--muted)", fontSize: 12.5, marginTop: 8 }}>
                  Showing 30 of {dry.events.length}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
