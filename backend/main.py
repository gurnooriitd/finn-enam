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
    return {
        "status": "ok",
        "ts": datetime.datetime.utcnow().isoformat(),
    }


@app.get("/api/portfolio")
def get_portfolio(since: str = ""):
    """Per-stock portfolio rows: last close, day %, 20d volume ratio,
    30-day sparkline closes, unseen filing count since `since` (ISO date)."""
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
                        "volume_ratio": None, "spark": [], "unseen_filings": 0})
            continue
        last_close = rows[0]["close"]
        day_pct = ((rows[0]["close"] - rows[1]["close"]) / rows[1]["close"] * 100
                   if len(rows) > 1 and rows[1]["close"] else None)
        vol_ratio = (rows[0]["volume"] / mean(r["volume"] for r in rows[1:21])
                     if len(rows) > 2 else None)
        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since[:10])
            except ValueError:
                since_dt = datetime.utcnow() - timedelta(days=3)
        else:
            since_dt = datetime.utcnow() - timedelta(days=3)
        n_ann = 0
        for (ts,) in conn.execute(
            "SELECT ts FROM announcements WHERE symbol=?", (sym,)
        ).fetchall():
            try:
                if datetime.strptime(ts, "%d-%b-%Y %H:%M:%S") >= since_dt:
                    n_ann += 1
            except (ValueError, TypeError):
                pass
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
            "spark": [round(r["close"], 2) for r in reversed(rows)],
            "unseen_filings": n_ann,
        })
    conn.close()
    return {"synthetic": pf.get("synthetic", True), "note": pf.get("note", ""), "holdings": out}


_dist = Path(__file__).parent.parent / "web" / "dist"
if (_dist / "index.html").exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
