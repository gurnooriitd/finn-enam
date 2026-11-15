"""
FINN Backend — main.py
FastAPI app. Serves the API and (later) the built React frontend.
"""
import datetime
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
def get_portfolio():
    import json
    from backend.config import PORTFOLIO_PATH
    try:
        return json.loads(PORTFOLIO_PATH.read_text())
    except Exception:
        return {"error": "portfolio.json not found"}
