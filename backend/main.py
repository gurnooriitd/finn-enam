"""
FINN Backend — main.py
FastAPI app. Serves the API and (later) the built React frontend.
"""
import datetime
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.db import init_db
from backend.api.digest import router as digest_router
from backend.api.alerts import router as alerts_router
from backend.api.costs import router as costs_router

app = FastAPI(title="FINN", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Init DB on startup
@app.on_event("startup")
def startup():
    init_db()


app.include_router(digest_router, prefix="/api")
app.include_router(alerts_router, prefix="/api")
app.include_router(costs_router, prefix="/api")


@app.get("/health")
def health():
    from backend.config import FINN_MODE, LLM_ENABLED
    return {
        "status": "ok",
        "mode": FINN_MODE,
        "llm_enabled": LLM_ENABLED,
        "ts": datetime.datetime.utcnow().isoformat(),
    }


@app.post("/api/refresh")
def refresh():
    """Live data refresh. Disabled in snapshot mode (hosted demo)."""
    import sqlite3
    from backend.config import FINN_MODE, DB_PATH

    conn = sqlite3.connect(DB_PATH)
    as_of = conn.execute("SELECT MAX(ts_iso) FROM announcements").fetchone()[0]
    conn.close()

    if FINN_MODE == "snapshot":
        return {
            "status": "snapshot",
            "refreshed": False,
            "message": f"live refresh disabled in hosted demo, data as of {as_of}",
        }

    try:
        from backend.ingest.prices import run as prices_run
        from backend.ingest.nse import run as nse_run
        from backend.push.classify import run_classify_all
        from backend.push.priority import run_score_all
        from backend.push.alerts_scan import scan_alerts
        from backend.config import PORTFOLIO_TICKERS

        prices_run(PORTFOLIO_TICKERS)
        nse_run(PORTFOLIO_TICKERS, days=30)
        run_classify_all(force=True)
        run_score_all()
        scan_alerts()
        return {"status": "ok", "refreshed": True, "message": f"refresh complete, data as of {as_of}"}
    except Exception as e:
        return {"status": "error", "refreshed": False, "message": f"refresh failed: {e}; data as of {as_of}"}


@app.get("/api/meta")
def meta():
    """Data freshness + mode, for anchoring client-side windows to the demo clock."""
    import sqlite3
    from backend.config import DB_PATH, FINN_MODE

    conn = sqlite3.connect(DB_PATH)
    as_of = conn.execute("SELECT MAX(ts_iso) FROM announcements").fetchone()[0]
    last_price = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    conn.close()
    if last_price and (not as_of or last_price > as_of[:10]):
        as_of = last_price
    return {"data_as_of": as_of, "mode": FINN_MODE}


@app.get("/api/portfolio")
def get_portfolio(since: str = ""):
    """Per-stock portfolio rows: last close, day %, 20d volume ratio,
    30-day sparkline closes, unseen filing count since `since` (ISO date).
    The window is anchored to data_as_of (demo clock), not real now."""
    import json
    import re
    import sqlite3
    from datetime import datetime, timedelta
    from statistics import mean
    from pathlib import Path
    from backend.config import DB_PATH, PORTFOLIO_PATH, BASE_DIR, RAW_DIR

    try:
        pf = json.loads(PORTFOLIO_PATH.read_text())
    except Exception:
        pf = {"holdings": []}
    weights = {h["symbol"]: h.get("weight") for h in pf.get("holdings", [])}
    symbols = [h["symbol"] for h in pf.get("holdings", [])]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    out = []
    for sym in symbols:
        rows = conn.execute(
            "SELECT date, close, volume FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 30",
            (sym,)
        ).fetchall()
        if not rows:
            out.append({"symbol": sym, "name": sym, "last_close": None, "day_pct": None,
                        "volume_ratio": None, "weight": weights.get(sym),
                        "spark": [], "unseen_filings": 0})
            continue
        last_close = rows[0]["close"]
        day_pct = ((rows[0]["close"] - rows[1]["close"]) / rows[1]["close"] * 100
                   if len(rows) > 1 and rows[1]["close"] else None)
        vol_ratio = (rows[0]["volume"] / mean(r["volume"] for r in rows[1:21])
                     if len(rows) > 2 else None)
        as_of = conn.execute("SELECT MAX(ts_iso) FROM announcements").fetchone()[0]
        last_price = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
        if last_price and (not as_of or last_price > as_of[:10]):
            as_of = last_price
        as_of_day = (as_of or datetime.utcnow().date().isoformat())[:10]
        if since:
            try:
                since_dt = datetime.fromisoformat(since[:10])
            except ValueError:
                since_dt = datetime.fromisoformat(as_of_day) - timedelta(days=3)
        else:
            since_dt = datetime.fromisoformat(as_of_day) - timedelta(days=3)
        as_of_dt = datetime.fromisoformat(as_of_day) + timedelta(days=1)
        if since_dt >= as_of_dt:
            since_dt = as_of_dt  # client already seen everything up to data_as_of
        n_ann = conn.execute(
            "SELECT COUNT(*) FROM announcements WHERE symbol=? AND ts_iso >= ?",
            (sym, since_dt.date().isoformat()),
        ).fetchone()[0]
        # Company name from cached screener HTML <title> (real data, no invention)
        name = sym
        for variant in ("consolidated", "standalone"):
            p = RAW_DIR / "screener" / f"{sym}_{variant}.html"
            if p.exists():
                m = re.search(r"<title>([^<\n|-]+)", p.read_text(errors="ignore"))
                if m:
                    name = re.sub(r"\s+share price\s*$", "", m.group(1).strip())
                    break
        out.append({
            "symbol": sym,
            "name": name,
            "last_close": round(last_close, 2),
            "day_pct": round(day_pct, 2) if day_pct is not None else None,
            "volume_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
            "weight": weights.get(sym),
            "spark": [round(r["close"], 2) for r in reversed(rows)],
            "unseen_filings": n_ann,
        })
    conn.close()
    return {
        "synthetic": pf.get("synthetic", True),
        "note": pf.get("note", ""),
        "data_as_of": as_of_day,
        "holdings": out,
    }


_dist = Path(__file__).parent.parent / "web" / "dist"
if (_dist / "index.html").exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
