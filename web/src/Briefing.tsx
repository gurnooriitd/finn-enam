import { useCallback, useEffect, useMemo, useState } from "react";
import { api, Digest, DigestItem, Holding } from "./api";
import { fmtTs } from "./api";
import { Chip, Delta, Empty, ErrorBox, Skeleton } from "./ui";

const WINDOWS = ["24h", "3d", "7d", "30d"];
const RAIL: Record<string, string> = {
  must_see: "var(--accent)",
  worth_a_look: "var(--info)",
  routine: "var(--line)",
};

export const tierRail = (tier: string) => RAIL[tier] || "var(--line)";

export function MustSeeCard({ item, h, onOpen }: { item: DigestItem; h?: Holding; onOpen: (i: DigestItem) => void }) {
  return (
    <button
      className="card rail"
      style={{ borderLeftColor: tierRail(item.tier), padding: "12px 14px", marginBottom: 10 }}
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
      <div style={{ margin: "4px 0 8px", fontSize: 14, color: "var(--text)" }}>
        {item.voice_summary || item.subject}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <Chip accent>{item.category}</Chip>
        <Chip>{fmtTs(item.ts)}</Chip>
        {h?.day_pct != null && (
          <Chip>
            <Delta pct={h.day_pct} />
          </Chip>
        )}
        {h?.volume_ratio != null && <Chip accent={h.volume_ratio >= 2}>vol {h.volume_ratio.toFixed(1)}x</Chip>}
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
  );
}

export function OneLiner({ item, onOpen }: { item: DigestItem; onOpen: (i: DigestItem) => void }) {
  return (
    <button
      className="card rail"
      style={{ borderLeftColor: tierRail(item.tier), padding: "9px 12px", marginBottom: 6, display: "flex", gap: 8, alignItems: "baseline" }}
      onClick={() => onOpen(item)}
    >
      <span className="num" style={{ fontWeight: 600, fontSize: 13, minWidth: 92, textAlign: "left" }}>
        {item.symbol}
      </span>
      <span style={{ fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {item.voice_summary || item.subject}
      </span>
      <span className="num" style={{ color: "var(--muted)", fontSize: 11.5 }}>
        {item.score?.toFixed(1)}
      </span>
    </button>
  );
}

interface Props {
  bySymbol: Map<string, Holding>;
  symbolFilter: string;
  clearFilter: () => void;
  onOpen: (i: DigestItem) => void;
}

export default function Briefing({ bySymbol, symbolFilter, clearFilter, onOpen }: Props) {
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
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 8 }}>
        <h1 style={{ fontSize: 22, margin: 0, fontWeight: 700 }}>
          {loading && !digest ? (
            <span className="num" style={{ color: "var(--muted)" }}>…</span>
          ) : (
            <>
              <span className="num">{filtered?.total ?? 0}</span> filings →{" "}
              <span className="num" style={{ color: "var(--accent)" }}>{filtered?.summary.must_see ?? 0}</span> must-see
            </>
          )}
        </h1>
        <div className="windowchips" role="tablist" aria-label="window">
          {WINDOWS.map((w) => (
            <button key={w} className={wanted === w ? "on" : ""} onClick={() => setWanted(w)}>
              {w}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "2px 0 4px", minHeight: 24 }}>
        {widenedNote && <Chip accent>{widenedNote}</Chip>}
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
                <MustSeeCard key={i.id} item={i} h={bySymbol.get(i.symbol)} onOpen={onOpen} />
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
                <OneLiner key={i.id} item={i} onOpen={onOpen} />
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
                        <OneLiner key={i.id} item={i} onOpen={onOpen} />
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
                    <MustSeeCard key={i.id} item={i} h={h} onOpen={onOpen} />
                  ) : (
                    <OneLiner key={i.id} item={i} onOpen={onOpen} />
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
