import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { api, Digest, DigestChild, DigestItem, Holding, ResultsSummary, num } from "./api";
import { fmtTs } from "./api";
import { Chip, Delta, Empty, ErrorBox, Skeleton } from "./ui";

const WINDOWS = ["24h", "3d", "7d", "30d"];
const RAIL: Record<string, string> = {
  must_see: "var(--accent)",
  worth_a_look: "var(--info)",
  routine: "var(--line)",
};

export const tierRail = (tier: string) => RAIL[tier] || "var(--line)";

const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtAsOf = (iso: string | null) => {
  if (!iso) return "";
  const d = new Date(iso.slice(0, 10) + "T00:00:00");
  return isNaN(d.getTime()) ? iso : `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}`;
};

function ChildList({ item }: { item: DigestItem }) {
  const all: (DigestChild & { lead?: boolean })[] = [
    {
      id: item.id, ts: item.ts, ts_iso: item.ts_iso, subject: item.subject,
      voice_summary: item.voice_summary, attachment_url: item.attachment_url,
      score: item.score, price_ctx: item.price_ctx, lead: true,
    },
    ...item.children,
  ];
  return (
    <div className="lane-expand" style={{ margin: "10px 0 2px", borderTop: "1px solid var(--line)", paddingTop: 6 }}>
      {all.map((c, i) => (
        <div key={c.id} style={{ display: "flex", gap: 8, alignItems: "baseline", padding: "5px 0" }}>
          <span className="num" style={{ color: "var(--muted)", fontSize: 11, minWidth: 74 }}>{fmtTs(c.ts)}</span>
          <span style={{ fontSize: 12.5, flex: 1 }}>{c.subject}</span>
          {c.attachment_url && (
            <a href={c.attachment_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} style={{ fontSize: 11.5 }}>
              PDF ↗
            </a>
          )}
        </div>
      ))}
    </div>
  );
}

function ArrowPct({ v }: { v: number | null }) {
  if (v == null) return <span className="num rt-na">n/a</span>;
  const up = v >= 0;
  return (
    <span className="num" style={{ color: up ? "var(--up)" : "var(--down)" }}>
      {up ? "▲" : "▼"} {up ? "+" : ""}
      {v.toFixed(1)}%
    </span>
  );
}

export function ResultsTable({ rs }: { rs: ResultsSummary }) {
  return (
    <div style={{ marginTop: 6 }}>
      <div className="rt">
        <span className="rt-h">{rs.quarter} results</span>
        <span className="rt-h num" style={{ textAlign: "right" }}>Rs cr</span>
        <span className="rt-h num" style={{ textAlign: "right" }}>QoQ</span>
        <span className="rt-h num" style={{ textAlign: "right" }}>YoY</span>
        {rs.rows.map((r) => (
          <Fragment key={r.metric}>
            <span style={{ fontSize: 13 }}>{r.metric}</span>
            <span className="num" style={{ textAlign: "right" }}>
              {r.value == null ? "n/a" : num(r.value, 0)}
            </span>
            <span style={{ textAlign: "right" }}><ArrowPct v={r.qoq_pct} /></span>
            <span style={{ textAlign: "right" }}><ArrowPct v={r.yoy_pct} /></span>
          </Fragment>
        ))}
      </div>
      <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 5, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {rs.ebitda_margin != null && <span className="num">EBITDA mgn {rs.ebitda_margin}%</span>}
        {rs.pat_margin != null && <span className="num">PAT mgn {rs.pat_margin}%</span>}
        <span className="chip" style={{ fontSize: 10.5 }}>screener standardised, {rs.basis}</span>
        {rs.note && <span>{rs.note}</span>}
      </div>
    </div>
  );
}

export function MustSeeCard({ item, h, onOpen, onOpenStock }: { item: DigestItem; h?: Holding; onOpen: (i: DigestItem) => void; onOpenStock?: (s: string) => void }) {
  const [open, setOpen] = useState(false);
  const pc = item.price_ctx;
  const headline = item.headline ?? item.voice_summary ?? item.subject;
  const rs = item.category === "results" ? item.results_summary : null;
  return (
    <div style={{ marginBottom: 10 }}>
      <button
        className="card rail"
        style={{ borderLeftColor: tierRail(item.tier), padding: "12px 14px", width: "100%" }}
        onClick={() => onOpen(item)}
      >
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
          <span className="num" style={{ fontWeight: 600, fontSize: 15 }}>
            {item.symbol}
          </span>
          <span className="num" style={{ color: "var(--muted)", fontSize: 12 }}>
            {item.score?.toFixed(1)}
          </span>
        </div>
        {rs ? (
          <ResultsTable rs={rs} />
        ) : (
          <div style={{ margin: "4px 0 8px", fontSize: 14, color: "var(--text)", textAlign: "left" }}>
            <span className="clamp2">{headline}</span>
            {headline.length > 90 && (
              <button
                className="more"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpen(item);
                }}
              >
                more
              </button>
            )}
          </div>
        )}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <Chip accent>{item.category}</Chip>
          {item.mentions_results && onOpenStock && (
            <button
              className="chip"
              onClick={(e) => {
                e.stopPropagation();
                onOpenStock(item.symbol);
              }}
            >
              → results card
            </button>
          )}
          <Chip>{fmtTs(item.ts)}</Chip>
          {pc?.day_pct != null && (
            <Chip>
              <Delta pct={pc.day_pct} />
            </Chip>
          )}
          {pc?.vol_ratio != null && <Chip accent={pc.vol_ratio >= 2}>vol {pc.vol_ratio.toFixed(1)}x</Chip>}
          {item.child_count > 1 && (
            <button
              className="chip accent"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(!open);
              }}
            >
              x{item.child_count}
              {open ? " ▲" : " ▼"}
            </button>
          )}
          {item.attachment_url && (
            <a
              href={item.attachment_url}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              style={{ fontSize: 12.5, marginLeft: "auto" }}
            >
              filing PDF ↗
            </a>
          )}
        </div>
      </button>
      {open && item.child_count > 1 && <ChildList item={item} />}
    </div>
  );
}

export function OneLiner({ item, onOpen, onOpenStock }: { item: DigestItem; onOpen: (i: DigestItem) => void; onOpenStock?: (s: string) => void }) {
  const [open, setOpen] = useState(false);
  const rs = item.category === "results" ? item.results_summary : null;
  if (rs) {
    return (
      <div>
        <button
          className="card rail"
          style={{ borderLeftColor: tierRail(item.tier), padding: "9px 12px 10px", marginBottom: 6, width: "100%" }}
          onClick={() => onOpen(item)}
        >
          <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span className="num" style={{ fontWeight: 600, fontSize: 13 }}>{item.symbol}</span>
            <span className="num" style={{ color: "var(--muted)", fontSize: 12 }}>{rs.quarter} results</span>
            {item.child_count > 1 && (
              <button
                className="chip"
                onClick={(e) => {
                  e.stopPropagation();
                  setOpen(!open);
                }}
              >
                x{item.child_count}
              </button>
            )}
            <span className="num" style={{ color: "var(--muted)", fontSize: 11.5, marginLeft: "auto" }}>
              {item.score?.toFixed(1)}
            </span>
          </div>
          <ResultsTable rs={rs} />
        </button>
        {open && item.child_count > 1 && <ChildList item={item} />}
      </div>
    );
  }
  const headline = item.headline ?? item.voice_summary ?? item.subject;
  return (
    <div>
      <button
        className="card rail"
        style={{ borderLeftColor: tierRail(item.tier), padding: "9px 12px", marginBottom: 6, display: "flex", gap: 8, alignItems: "baseline", width: "100%" }}
        onClick={() => onOpen(item)}
      >
        <span className="num" style={{ fontWeight: 600, fontSize: 13, minWidth: 92, textAlign: "left" }}>
          {item.symbol}
        </span>
        <span style={{ fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {headline}
        </span>
        {item.mentions_results && onOpenStock && (
          <button
            className="chip"
            onClick={(e) => {
              e.stopPropagation();
              onOpenStock(item.symbol);
            }}
          >
            → results
          </button>
        )}
        {item.child_count > 1 && (
          <button
            className="chip"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(!open);
            }}
          >
            x{item.child_count}
          </button>
        )}
        <span className="num" style={{ color: "var(--muted)", fontSize: 11.5 }}>
          {item.score?.toFixed(1)}
        </span>
      </button>
      {open && item.child_count > 1 && <ChildList item={item} />}
    </div>
  );
}

interface Props {
  bySymbol: Map<string, Holding>;
  symbolFilter: string;
  clearFilter: () => void;
  onOpen: (i: DigestItem) => void;
  onOpenStock: (s: string) => void;
}

export default function Briefing({ bySymbol, symbolFilter, clearFilter, onOpen, onOpenStock }: Props) {
  const [wanted, setWanted] = useState("24h");
  const [effWindow, setEffWindow] = useState("24h");
  const [widenedNote, setWidenedNote] = useState("");
  const [digest, setDigest] = useState<Digest | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [groupBy, setGroupBy] = useState<"priority" | "company">("priority");
  const [openRoutineCats, setOpenRoutineCats] = useState<Set<string>>(new Set());

  const load = useCallback(async (w: string, widenFrom?: string) => {
    setLoading(true);
    setErr("");
    if (!widenFrom) setWidenedNote("");
    try {
      const d = await api.digest(w);
      if (d.total === 0) {
        const idx = WINDOWS.indexOf(w);
        if (idx >= 0 && idx < WINDOWS.length - 1) {
          const next = WINDOWS[idx + 1];
          setEffWindow(next);
          setWidenedNote(`Nothing in ${w}, showing ${next}`);
          await load(next, widenFrom ?? w);
          return;
        }
      } else if (widenFrom) {
        setWidenedNote(`Nothing in ${widenFrom}, showing ${w}`);
      }
      setDigest(d);
      setEffWindow(w);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(wanted);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, symbolFilter]);

  const filtered = useMemo(() => {
    if (!digest) return null;
    if (!symbolFilter) return digest;
    const keep = (items: DigestItem[]) => items.filter((i) => i.symbol === symbolFilter);
    return {
      ...digest,
      must_see: keep(digest.must_see),
      worth_a_look: keep(digest.worth_a_look),
      routine: { count: 0, items: keep(digest.routine.items) },
    };
  }, [digest, symbolFilter]);

  const toggleRoutine = (cat: string) =>
    setOpenRoutineCats((s) => {
      const n = new Set(s);
      n.has(cat) ? n.delete(cat) : n.add(cat);
      return n;
    });

  const routineByCat = useMemo(() => {
    const m = new Map<string, DigestItem[]>();
    for (const it of filtered?.routine.items || []) {
      const cat = it.category || "unclassified";
      if (!m.has(cat)) m.set(cat, []);
      m.get(cat)!.push(it);
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [filtered]);

  const companyGroups = useMemo(() => {
    const all = [...(filtered?.must_see || []), ...(filtered?.worth_a_look || []), ...(filtered?.routine.items || [])];
    const m = new Map<string, DigestItem[]>();
    for (const it of all) {
      if (!m.has(it.symbol)) m.set(it.symbol, []);
      m.get(it.symbol)!.push(it);
    }
    return [...m.entries()].sort((a, b) => (b[1][0]?.score || 0) - (a[1][0]?.score || 0));
  }, [filtered]);

  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {loading && !digest ? (
          <span className="num" style={{ color: "var(--muted)" }}>…</span>
        ) : (
          <>
            <span className="num">{filtered?.total ?? 0}</span> filings →{" "}
            <span className="num" style={{ color: "var(--accent)" }}>{filtered?.summary.must_see ?? 0}</span> must-see
          </>
        )}
      </h1>
      <div className="windowchips" role="tablist" aria-label="window" style={{ marginTop: 8 }}>
        {WINDOWS.map((w) => (
          <button key={w} className={effWindow === w ? "on" : ""} onClick={() => setWanted(w)}>
            {w}
          </button>
        ))}
      </div>

      {/* Directly under the window chips: widened note + data freshness */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", margin: "0 0 6px", minHeight: 22 }}>
        {widenedNote && <Chip accent>{widenedNote}</Chip>}
        {filtered?.data_as_of && (
          <Chip>data as of {fmtAsOf(filtered.data_as_of)}</Chip>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "2px 0 4px", minHeight: 24 }}>
        {symbolFilter && (
          <button className="chip" onClick={clearFilter} style={{ minHeight: 24 }}>
            {symbolFilter} ✕
          </button>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          {(["priority", "company"] as const).map((g) => (
            <button
              key={g}
              className="chip"
              style={groupBy === g ? { color: "var(--text)", borderColor: "var(--muted)" } : undefined}
              onClick={() => setGroupBy(g)}
            >
              by {g}
            </button>
          ))}
        </div>
      </div>

      {loading && <Skeleton rows={4} />}
      {err && <ErrorBox msg={err} retry={() => load(wanted)} />}
      {!loading && !err && filtered && filtered.total === 0 && (
        <Empty title="Nothing filed in this window" hint="Widen the window or check another stock." />
      )}

      {!loading && !err && filtered && filtered.total > 0 && groupBy === "priority" && (
        <>
          {filtered.must_see.length > 0 && (
            <>
              <div className="lane-head">
                <span className="lane-title" style={{ color: "var(--accent)" }}>Must-see</span>
                <span className="num" style={{ fontSize: 12, color: "var(--muted)" }}>{filtered.summary.must_see}</span>
              </div>
              {filtered.must_see.map((i) => (
                <MustSeeCard key={i.id} item={i} h={bySymbol.get(i.symbol)} onOpen={onOpen} onOpenStock={onOpenStock} />
              ))}
            </>
          )}

          {filtered.worth_a_look.length > 0 && (
            <>
              <div className="lane-head">
                <span className="lane-title" style={{ color: "var(--info)" }}>Worth a look</span>
                <span className="num" style={{ fontSize: 12, color: "var(--muted)" }}>{filtered.summary.worth_a_look}</span>
              </div>
              {filtered.worth_a_look.map((i) => (
                <OneLiner key={i.id} item={i} onOpen={onOpen} onOpenStock={onOpenStock} />
              ))}
            </>
          )}

          {filtered.routine.items.length > 0 && (
            <>
              <div className="lane-head">
                <span className="lane-title">Routine</span>
                <span className="num" style={{ fontSize: 12, color: "var(--muted)" }}>
                  {filtered.routine.count} hidden unless expanded
                </span>
              </div>
              {routineByCat.map(([cat, items]) => (
                <div key={cat} style={{ marginBottom: 8 }}>
                  <button
                    className="card"
                    style={{ padding: "10px 14px", display: "flex", justifyContent: "space-between", alignItems: "center", minHeight: 44 }}
                    onClick={() => toggleRoutine(cat)}
                    aria-expanded={openRoutineCats.has(cat)}
                  >
                    <span style={{ fontWeight: 600, fontSize: 13.5 }}>{cat}</span>
                    <span className="num" style={{ color: "var(--muted)", fontSize: 13 }}>
                      {items.length} {openRoutineCats.has(cat) ? "▲" : "▼"}
                    </span>
                  </button>
                  {openRoutineCats.has(cat) && (
                    <div className="lane-expand" style={{ padding: "8px 2px" }}>
                      {items.map((i) => (
                        <OneLiner key={i.id} item={i} onOpen={onOpen} onOpenStock={onOpenStock} />
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </>
          )}
        </>
      )}

      {!loading && !err && filtered && filtered.total > 0 && groupBy === "company" && (
        <>
          {companyGroups.map(([sym, items]) => {
            const h = bySymbol.get(sym);
            return (
              <div key={sym} style={{ marginBottom: 16 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "baseline", margin: "10px 2px 8px" }}>
                  <span className="num" style={{ fontWeight: 700 }}>{sym}</span>
                  {h?.day_pct != null && (
                    <span style={{ fontSize: 12.5 }}>
                      <Delta pct={h.day_pct} />
                    </span>
                  )}
                  <span className="num" style={{ color: "var(--muted)", fontSize: 12, marginLeft: "auto" }}>
                    {items.length} filing{items.length > 1 ? "s" : ""}
                  </span>
                </div>
                {items.map((i) =>
                  i.tier === "must_see" ? (
                    <MustSeeCard key={i.id} item={i} h={h} onOpen={onOpen} onOpenStock={onOpenStock} />
                  ) : (
                    <OneLiner key={i.id} item={i} onOpen={onOpen} onOpenStock={onOpenStock} />
                  )
                )}
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
