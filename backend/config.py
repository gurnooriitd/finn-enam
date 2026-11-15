"""
FINN Backend — config.py
All settings from environment variables.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

# Force IPv4-first DNS: this network's IPv6 path blackholes, and Python's
# socket layer (unlike curl) has no happy-eyeballs, so every HTTPS call
# otherwise stalls for ~2x the timeout before falling back to v4.
import socket as _socket
_orig_getaddrinfo = _socket.getaddrinfo
def _ipv4_first_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    res = _orig_getaddrinfo(host, port, family, type, proto, flags)
    return sorted(res, key=lambda a: 0 if a[0] == _socket.AF_INET else 1)
_socket.getaddrinfo = _ipv4_first_getaddrinfo

# LLM gateway
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY", "")
CHEAP_MODEL = os.getenv("CHEAP_MODEL", "gemini-2.0-flash")
STRONG_MODEL = os.getenv("STRONG_MODEL", "gemini-2.0-flash")

# Master kill-switch: when 0/false, every LLM call site must skip the LLM
# (rules-only). Ingestion/classification must never block on the LLM.
LLM_ENABLED = os.getenv("LLM_ENABLED", "1").lower() in ("1", "true", "yes", "on")

# Deployment mode: 'live' fetches NSE/yfinance/screener on refresh;
# 'snapshot' serves the committed data snapshot with no external calls
# and no LLM (hosted demo is fully self-contained).
FINN_MODE = os.getenv("FINN_MODE", "live")
if FINN_MODE == "snapshot":
    LLM_ENABLED = False

# DB
import shutil as _shutil
import tempfile as _tempfile

DB_PATH = BASE_DIR / "data" / "finn.db"
if DB_PATH.exists() and not os.access(DB_PATH, os.W_OK):
    # Read-only container FS: copy the snapshot to a writable location
    _tmp = Path(_tempfile.gettempdir()) / "finn.db"
    if not _tmp.exists() or _tmp.stat().st_mtime < DB_PATH.stat().st_mtime:
        _shutil.copy2(DB_PATH, _tmp)
    DB_PATH = _tmp
RAW_DIR = BASE_DIR / "data" / "raw"
try:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    RAW_DIR = Path(_tempfile.gettempdir()) / "finn_raw"
    RAW_DIR.mkdir(parents=True, exist_ok=True)

# Portfolio
PORTFOLIO_PATH = BASE_DIR / "data" / "portfolio.json"

# Rate limits
NSE_SLEEP = 1.5  # seconds between NSE requests

# Portfolio tickers
PORTFOLIO_TICKERS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "SUNPHARMA",
    "LT", "MARUTI", "TITAN", "ITC", "BAJFINANCE",
    "ETERNAL", "DIXON", "POLYCAB", "HAL", "SUZLON",
    "TATASTEEL", "ADANIPORTS", "YESBANK",
]
