// ui.tsx — small shared presentational components. No chart library.
import { ReactNode } from "react";

export function Sparkline({ points, w = 76, h = 26 }: { points: number[]; w?: number; h?: number }) {
  if (!points || points.length < 2) return <svg width={w} height={h} />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = w / (points.length - 1);
  const coords = points.map((p, i) => `${(i * step).toFixed(1)},${(h - 2 - ((p - min) / span) * (h - 4)).toFixed(1)}`);
  const rising = points[points.length - 1] >= points[0];
  const stroke = rising ? "var(--up)" : "var(--down)";
  return (
    <svg width={w} height={h} aria-hidden="true" style={{ flexShrink: 0 }}>
      <polyline points={coords.join(" ")} fill="none" stroke={stroke} strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

export function Delta({ pct, digits = 1 }: { pct: number | null | undefined; digits?: number }) {
  if (pct == null) return <span className="num" style={{ color: "var(--muted)" }}>—</span>;
  const up = pct >= 0;
  const arrow = up ? "▲" : "▼"; // direction is not colour-only
  const color = up ? "var(--up)" : "var(--down)";
  return (
    <span className="num" style={{ color }}>
      {arrow} {up ? "+" : ""}
      {pct.toFixed(digits)}%
    </span>
  );
}

export function Chip({ children, accent }: { children: ReactNode; accent?: boolean }) {
  return <span className={`chip${accent ? " accent" : ""}`}>{children}</span>;
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skel" style={{ height: i === 0 ? 84 : 52 }} />
      ))}
    </div>
  );
}

export function ErrorBox({ msg, retry }: { msg: string; retry?: () => void }) {
  return (
    <div className="card rail" style={{ padding: 16, borderLeftColor: "var(--down)" }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>Couldn't load data</div>
      <div style={{ color: "var(--muted)", fontSize: 13 }}>{msg}</div>
      {retry && (
        <button className="btn" style={{ marginTop: 12 }} onClick={retry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="card" style={{ padding: 22, textAlign: "center", color: "var(--muted)" }}>
      <div style={{ color: "var(--text)", fontWeight: 600, marginBottom: 4 }}>{title}</div>
      {hint && <div style={{ fontSize: 13 }}>{hint}</div>}
    </div>
  );
}

export function Sheet({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  return (
    <>
      <div className="sheet-overlay" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true">
        <div
          style={{
            width: 36,
            height: 4,
            borderRadius: 2,
            background: "var(--line)",
            margin: "0 auto 14px",
          }}
        />
        {children}
      </div>
    </>
  );
}
