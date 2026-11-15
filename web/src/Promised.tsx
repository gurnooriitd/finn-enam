// Promised vs Delivered. Quotes are from the transcript; the verdict is computed.
import { useState } from "react";
import { PromiseBar, PromiseItem, Promised } from "./api";

const VERDICT_COLOR: Record<string, string> = {
  Delivered: "var(--up)",
  Partly: "var(--accent)",
  Missed: "var(--down)",
  Pending: "var(--info)",
  "Can't verify": "var(--muted)",
};

function pos(bar: PromiseBar, v: number) {
  return Math.min(100, Math.max(0, ((v - bar.min) / (bar.max - bar.min)) * 100));
}

function Band({ bar }: { bar: PromiseBar }) {
  const a = pos(bar, bar.actual);
  const l = pos(bar, bar.low);
  const h = pos(bar, bar.high);
  return (
    <div style={{ position: "relative", height: 14, marginTop: 8 }} aria-hidden="true">
      <div style={{ position: "absolute", left: 0, right: 0, top: 6, height: 2, background: "var(--line)" }} />
      <div style={{
        position: "absolute", top: 4, height: 6, borderRadius: 3,
        left: `${Math.min(l, h)}%`, width: `${Math.max(2, Math.abs(h - l))}%`,
        background: "color-mix(in srgb, var(--accent) 55%, transparent)",
      }} />
      <div style={{
        position: "absolute", top: 2, width: 2, height: 10, background: "var(--text)",
        left: `calc(${a}% - 1px)`,
      }} />
    </div>
  );
}

function Card({ item }: { item: PromiseItem }) {
  const [open, setOpen] = useState(false);
  const color = VERDICT_COLOR[item.verdict] || "var(--muted)";
  return (
    <article className="card rail" style={{ borderLeftColor: color, padding: "10px 12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: 11, color: "var(--muted)" }}>
          {item.metric_label}{item.qualifier === "hedged" ? " · hedged" : ""}
          {item.interpreted ? " · range read from the wording" : ""}
        </span>
        <span className="chip" style={{ color, borderColor: color }}>{item.verdict}</span>
      </div>
      <p style={{ margin: "8px 0 2px", fontSize: 14, lineHeight: 1.4 }}>“{item.quote}”</p>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>
        {item.speaker ? `${item.speaker} · ` : ""}{item.chunk_id}{item.page ? ` · p.${item.page}` : ""}
        {item.horizon ? ` · ${item.horizon}` : ""}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 10 }}>
        <div>
          <div style={{ fontSize: 10, letterSpacing: "0.06em", color: "var(--muted)" }}>PROMISED</div>
          <div className="num" style={{ fontSize: 14, fontWeight: 600 }}>{item.promise_label || "—"}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, letterSpacing: "0.06em", color: "var(--muted)" }}>DELIVERED</div>
          <div className="num" style={{ fontSize: 14, fontWeight: 600 }}>{item.actual_label || "—"}</div>
        </div>
      </div>
      {item.bar && <Band bar={item.bar} />}
      {item.note && <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>{item.note}</div>}
      <button
        onClick={() => setOpen(!open)}
        style={{
          marginTop: 8, background: "none", border: "none", color: "var(--info)",
          padding: 0, fontSize: 12, cursor: "pointer", minHeight: 32,
        }}
      >
        {open ? "Hide excerpt" : "Source excerpt"}
      </button>
      {open && (
        <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4, lineHeight: 1.45 }}>
          {item.excerpt || "Excerpt not in the snapshot."}
        </div>
      )}
    </article>
  );
}

export default function PromisedView({ data }: { data: Promised }) {
  const hit = data.hit_rate;
  return (
    <section>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
        <h2 style={{ fontSize: 13, fontWeight: 700, margin: 0, letterSpacing: "0.04em", textTransform: "uppercase" }}>
          Promised vs delivered
        </h2>
        {hit != null && (
          <span className="num" style={{ fontSize: 12, color: "var(--muted)" }}>{Math.round(hit * 100)}% hit</span>
        )}
      </div>
      {hit != null && (
        <div style={{ height: 4, borderRadius: 2, background: "var(--line)", marginBottom: 8 }}>
          <div style={{ width: `${Math.round(hit * 100)}%`, height: "100%", borderRadius: 2, background: "var(--accent)" }} />
        </div>
      )}
      {data.pattern && <p style={{ margin: "0 0 10px", fontSize: 13, lineHeight: 1.45 }}>{data.pattern}</p>}
      {data.status !== "ready" && (
        <div className="card" style={{ padding: 14, color: "var(--muted)", fontSize: 13 }}>
          {data.note}
          {data.status === "no_corpus" && data.corpus && data.corpus.length > 0 && (
            <div style={{ marginTop: 6 }}>Transcripts are loaded for {data.corpus.join(", ")}.</div>
          )}
        </div>
      )}
      <div style={{ display: "grid", gap: 12 }}>
        {data.groups.map((g) => {
          const main = g.items.filter((i) => i.verdict !== "Can't verify");
          const aside = g.items.filter((i) => i.verdict === "Can't verify");
          if (main.length === 0 && aside.length === 0) return null;
          return (
            <div key={g.call_period}>
              <div style={{ fontSize: 11, color: "var(--muted)", margin: "2px 2px 6px" }}>From the {g.label}</div>
              <div style={{ display: "grid", gap: 8 }}>
                {main.map((item) => (
                  <Card key={item.chunk_id + item.quote} item={item} />
                ))}
                {aside.length > 0 && (
                  <details>
                    <summary style={{ fontSize: 12, color: "var(--muted)", cursor: "pointer", minHeight: 32 }}>
                      {aside.length} not scored — no matching column
                    </summary>
                    <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
                      {aside.map((item) => (
                        <Card key={item.chunk_id + item.quote} item={item} />
                      ))}
                    </div>
                  </details>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {data.status === "ready" && (
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>{data.note}</div>
      )}
    </section>
  );
}
