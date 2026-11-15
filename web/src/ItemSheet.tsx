import { DigestItem, num } from "./api";
import { fmtTs } from "./api";
import { Chip, Sheet } from "./ui";
import { ResultsTable, tierRail } from "./Briefing";

function BreakdownRow({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "7px 0", borderBottom: "1px solid var(--line)" }}>
      <span style={{ color: "var(--muted)", fontSize: 13.5 }}>{label}</span>
      <span className="num" style={{ fontSize: 13.5, textAlign: "right" }}>
        {value} {note && <span style={{ color: "var(--muted)" }}>{note}</span>}
      </span>
    </div>
  );
}

export default function ItemSheet({ item, onClose }: { item: DigestItem; onClose: () => void }) {
  const b = item.score_breakdown || {};
  return (
    <Sheet onClose={onClose}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
        <span className="num" style={{ fontSize: 20, fontWeight: 700 }}>{item.symbol}</span>
        <Chip accent>{item.category}</Chip>
        <span className="num" style={{ marginLeft: "auto", color: "var(--accent)", fontWeight: 600, fontSize: 15 }}>
          {item.score?.toFixed(1)}
        </span>
      </div>
      <div style={{ color: "var(--muted)", fontSize: 12.5, marginBottom: 12 }}>{fmtTs(item.ts)} · NSE</div>

      <div style={{ fontSize: 15, lineHeight: 1.5, marginBottom: 14 }}>{item.voice_summary || item.subject}</div>

      {item.category === "results" && item.results_summary && <ResultsTable rs={item.results_summary} />}
      {item.category === "results" && !item.results_summary && (
        <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 12 }}>
          Numbers not yet in standardised data — see the source filing.
        </div>
      )}

      {item.voice_summary && (
        <div style={{ fontSize: 13.5, color: "var(--muted)", marginBottom: 14, padding: 10, background: "var(--surface2)", borderRadius: 8 }}>
          {item.subject}
        </div>
      )}

      <div className="lane-title" style={{ margin: "4px 0 6px" }}>Why this ranked {item.tier === "must_see" ? "high" : "here"}</div>
      <div style={{ borderLeft: `3px solid ${tierRail(item.tier)}`, paddingLeft: 12, marginBottom: 14 }}>
        <BreakdownRow label="Category base" value={num(b.category_base)} />
        {b.portfolio_weight != null && (
          <BreakdownRow label="Portfolio weight" value={`${(b.portfolio_weight * 100).toFixed(0)}%`} note={`× ${b.weight_mult}`} />
        )}
        {b.keyword_hits && b.keyword_hits.length > 0 && (
          <BreakdownRow label="Keyword hits" value={b.keyword_hits.join(", ")} note={`+${b.keyword_boost}`} />
        )}
        {b.mgmt_senior_boost ? <BreakdownRow label="Senior role" value={`+${b.mgmt_senior_boost}`} /> : null}
        {b.rating_downgrade_boost ? <BreakdownRow label="Downgrade watch" value={`+${b.rating_downgrade_boost}`} /> : null}
        {b.vol_ratio != null && <BreakdownRow label="Volume vs 20d avg" value={`${b.vol_ratio.toFixed(2)}x`} />}
        {b.price_move_pct != null && <BreakdownRow label="Price that day" value={`${b.price_move_pct > 0 ? "+" : ""}${b.price_move_pct}%`} />}
        {b.anomaly_boost ? <BreakdownRow label="Anomaly boost" value={`+${b.anomaly_boost}`} note={b.anomaly_note} /> : null}
        <BreakdownRow label="Final score" value={num(b.final_score ?? item.score)} />
      </div>

      {item.attachment_url ? (
        <a className="btn primary" href={item.attachment_url} target="_blank" rel="noreferrer" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}>
          Open source filing ↗
        </a>
      ) : (
        <div style={{ color: "var(--muted)", fontSize: 13 }}>No attachment on this filing.</div>
      )}
    </Sheet>
  );
}
