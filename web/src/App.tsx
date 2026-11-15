import { useCallback, useEffect, useState } from "react";
import { api, AlertEvent, DigestItem, Holding } from "./api";
import Briefing from "./Briefing";
import Alerts from "./Alerts";
import Portfolio from "./Portfolio";
import Research from "./Research";
import ItemSheet from "./ItemSheet";

const SEEN_EVENTS_KEY = "finn.seenEvents";

export type Tab = "briefing" | "alerts" | "research" | "portfolio";

export default function App() {
  const [tab, setTab] = useState<Tab>("briefing");
  const [symbolFilter, setSymbolFilter] = useState<string>("");
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [portfolioAsOf, setPortfolioAsOf] = useState<string | null>(null);
  const [unread, setUnread] = useState(0);
  const [openItem, setOpenItem] = useState<DigestItem | null>(null);

  const loadPortfolio = useCallback(() => {
    api
      .portfolio()
      .then((p) => {
        setHoldings(p.holdings);
        setPortfolioAsOf(p.data_as_of || null);
      })
      .catch(() => setHoldings([]));
  }, []);

  const loadUnread = useCallback(() => {
    Promise.all([api.events(200), api.meta()])
      .then(([events, meta]) => {
        const seen = new Set<string>(JSON.parse(localStorage.getItem(SEEN_EVENTS_KEY) || "[]"));
        // Unread window = 7 days ending at data_as_of (demo clock), not real now.
        const asOfDay = (meta.data_as_of || new Date().toISOString()).slice(0, 10);
        const cutoff = new Date(asOfDay + "T00:00:00").getTime() - 7 * 24 * 3600 * 1000;
        setUnread(
          events.filter((e) => {
            const t = new Date((e.date || e.fired_at).slice(0, 10) + "T00:00:00").getTime();
            return !seen.has(String(e.id)) && t >= cutoff;
          }).length
        );
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadPortfolio();
    loadUnread();
  }, [loadPortfolio, loadUnread]);

  const markEventsSeen = useCallback(() => {
    api
      .events(200)
      .then((events) => {
        localStorage.setItem(SEEN_EVENTS_KEY, JSON.stringify(events.map((e) => String(e.id))));
        setUnread(0);
      })
      .catch(() => {});
  }, []);

  const openStock = (sym: string) => {
    setSymbolFilter(sym);
    setTab("briefing");
  };

  const bySymbol = new Map(holdings.map((h) => [h.symbol, h]));

  return (
    <div className="app-shell">
      <div className="main-scroll">
        {tab === "briefing" && (
          <Briefing bySymbol={bySymbol} symbolFilter={symbolFilter} clearFilter={() => setSymbolFilter("")} onOpen={setOpenItem} onOpenStock={openStock} />
        )}
        {tab === "alerts" && <Alerts onSeen={markEventsSeen} onOpenStock={openStock} />}
        {tab === "research" && <Research />}
        {tab === "portfolio" && (
          <Portfolio holdings={holdings} dataAsOf={portfolioAsOf} onOpenStock={openStock} reload={loadPortfolio} />
        )}
      </div>

      <nav className="tabbar" aria-label="tabs">
        {(
          [
            ["briefing", "Briefing"],
            ["alerts", "Alerts"],
            ["research", "Research"],
            ["portfolio", "Portfolio"],
          ] as [Tab, string][]
        ).map(([t, label]) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
              {label}
              {t === "alerts" && unread > 0 && (
                <span className="badge">{unread > 99 ? "99+" : unread}</span>
              )}
            </span>
          </button>
        ))}
      </nav>

      {openItem && <ItemSheet item={openItem} onClose={() => setOpenItem(null)} />}
    </div>
  );
}
