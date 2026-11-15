import { Holding } from "./api";
import { inr, num } from "./api";
import { Chip, Delta, Empty, Sparkline } from "./ui";

export default function Portfolio({
  holdings,
  onOpenStock,
  reload,
}: {
  holdings: Holding[];
  onOpenStock: (s: string) => void;
  reload: () => void;
}) {
  if (holdings.length === 0) {
    return (
      <div>
        <h1 style={{ fontSize: 20, margin: "0 0 12px", fontWeight: 700 }}>Portfolio</h1>
        <Empty title="No holdings loaded" hint="Run scripts/refresh.py, then reload." />
        <button className="btn" style={{ marginTop: 10 }} onClick={reload}>
          Reload
        </button>
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <h1 style={{ fontSize: 20, margin: 0, fontWeight: 700 }}>Portfolio</h1>
        <Chip accent>synthetic weights</Chip>
        <span style={{ color: "var(--muted)", fontSize: 12, marginLeft: "auto" }}>EOD prices</span>
      </div>

      {holdings.map((h) => (
        <button
          key={h.symbol}
          className="card"
          style={{ padding: "10px 14px", marginBottom: 8, display: "flex", gap: 12, alignItems: "center", minHeight: 58 }}
          onClick={() => onOpenStock(h.symbol)}
        >
          <div style={{ flex: 1, minWidth: 0, textAlign: "left" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <span className="num" style={{ fontWeight: 700, fontSize: 14.5 }}>{h.symbol}</span>
              {h.unseen_filings > 0 && <span className="badge">{h.unseen_filings} new</span>}
            </div>
            <div style={{ color: "var(--muted)", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {h.name && h.name !== h.symbol ? h.name : ""}
            </div>
          </div>
          <Sparkline points={h.spark} />
          <div style={{ textAlign: "right", minWidth: 74 }}>
            <div className="num" style={{ fontSize: 14 }}>{inr(h.last_close)}</div>
            <div style={{ fontSize: 12.5 }}>
              <Delta pct={h.day_pct} />
            </div>
          </div>
          {h.volume_ratio != null && h.volume_ratio >= 2 && (
            <Chip accent>{num(h.volume_ratio, 1)}x vol</Chip>
          )}
        </button>
      ))}
    </div>
  );
}
