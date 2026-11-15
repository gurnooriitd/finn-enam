"""
FINN Backend — db.py
SQLite schema via plain sqlite3. Tables: prices, announcements,
nse_financial_results, screener_fundamentals, docs, llm_cache, llm_ledger,
alert_rules, alert_events.
"""
import sqlite3
from pathlib import Path
from backend.config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS prices (
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        adj_close REAL,
        volume INTEGER,
        PRIMARY KEY (symbol, date)
    );

    CREATE TABLE IF NOT EXISTS announcements (
        id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        ts TEXT NOT NULL,
        subject TEXT,
        raw_desc TEXT,
        voice_summary TEXT,
        attachment_url TEXT,
        exchange TEXT DEFAULT 'NSE',
        category TEXT,
        priority_tier TEXT,
        priority_score REAL,
        score_breakdown TEXT,
        summary_json TEXT,
        seq_id TEXT,
        UNIQUE(exchange, seq_id)
    );

    CREATE TABLE IF NOT EXISTS nse_financial_results (
        id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        period TEXT,
        from_date TEXT,
        to_date TEXT,
        filing_date TEXT,
        consolidated INTEGER,
        audited INTEGER,
        xbrl TEXT,
        result_link TEXT,
        result_description TEXT,
        financial_year TEXT,
        UNIQUE(symbol, period, consolidated, audited)
    );

    CREATE TABLE IF NOT EXISTS screener_fundamentals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        table_type TEXT NOT NULL,
        period TEXT NOT NULL,
        metric TEXT NOT NULL,
        value REAL,
        unit TEXT,
        fetched_at TEXT,
        UNIQUE(symbol, table_type, period, metric)
    );

    CREATE TABLE IF NOT EXISTS docs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        kind TEXT NOT NULL,
        period TEXT,
        url TEXT UNIQUE,
        path TEXT,
        text_hash TEXT,
        n_pages INTEGER,
        fetched_at TEXT
    );

    CREATE TABLE IF NOT EXISTS llm_cache (
        hash TEXT PRIMARY KEY,
        model TEXT,
        response TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS llm_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        step TEXT,
        model TEXT,
        tokens_in INTEGER,
        tokens_out INTEGER,
        est_cost REAL,
        cached INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS alert_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT DEFAULT 'global',
        symbol TEXT,
        kind TEXT NOT NULL,
        threshold REAL,
        categories TEXT,
        enabled INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS alert_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id INTEGER,
        symbol TEXT,
        date TEXT,
        announcement_id TEXT,
        kind TEXT,
        value REAL,
        threshold REAL,
        message TEXT,
        fired_at TEXT,
        UNIQUE(rule_id, symbol, date, announcement_id)
    );
    CREATE TABLE IF NOT EXISTS people_enrichment (
        announcement_id TEXT PRIMARY KEY,
        symbol TEXT,
        person TEXT,
        designation TEXT,
        action TEXT,
        effective_date TEXT,
        reason TEXT,
        predecessor TEXT,
        bio TEXT,
        confidence TEXT,
        sources_json TEXT,
        created_at TEXT
    );
    """)

    # Migration: sortable ISO timestamp column (NSE ts is 'DD-Mon-YYYY HH:MM:SS',
    # which does not sort chronologically as a string).
    cur.execute("PRAGMA table_info(announcements)")
    cols = {r[1] for r in cur.fetchall()}
    if "ts_iso" not in cols:
        cur.execute("ALTER TABLE announcements ADD COLUMN ts_iso TEXT")
        rows = cur.execute("SELECT id, ts FROM announcements").fetchall()
        from backend.push.context import parse_ts
        updates = []
        for rid, ts in rows:
            d = parse_ts(ts or "")
            updates.append((d.isoformat(sep=" ") if d else None, rid))
        cur.executemany("UPDATE announcements SET ts_iso=? WHERE id=?", updates)

    # Seed default alert rules if none exist
    cur.execute("SELECT COUNT(*) FROM alert_rules")
    if cur.fetchone()[0] == 0:
        defaults = [
            ("global", None, "volume_ratio", 2.0, None),
            ("global", None, "price_move", 5.0, None),
            ("global", None, "announcement_category", None, "results,M&A,management_change,litigation,credit_rating"),
        ]
        cur.executemany(
            "INSERT INTO alert_rules(scope, symbol, kind, threshold, categories) VALUES (?,?,?,?,?)",
            defaults
        )

    conn.commit()
    conn.close()
