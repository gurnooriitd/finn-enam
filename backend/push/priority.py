"""
Push — Priority Scoring
Deterministic, explainable scoring.
score = category_base × portfolio_weight_boost + interest_boost + anomaly_boost + keyword_boost
Stores breakdown dict alongside score.
"""
import json
import sqlite3
import datetime
from statistics import mean

from backend.config import DB_PATH, PORTFOLIO_TICKERS

# --- Category base scores ---
CATEGORY_BASE = {
    "results": 10.0,
    "M&A": 10.0,
    "management_change": 8.0,
    "fund_raise": 7.0,
    "credit_rating": 7.0,
    "litigation": 6.0,
    "capex": 5.0,
    "pledging": 5.0,
    "insider_trading": 4.5,
    "dividend": 4.0,
    "bonus_split": 3.5,
    "earnings_call": 2.0,
    "investor_meetings": 1.5,
    "press_release": 1.5,
    "agm_egm": 1.0,
    "postal_ballot": 0.5,
    "unclassified": 0.5,
}

# Keywords that boost any announcement
HIGH_BOOST_KEYWORDS = [
    "resign", "fraud", "default", "penalty", "downgrade",
    "insolvency", "search and seizure", "fraud", "defalcation",
    "investigation", "nclt", "sebi", "default", "npa",
]

MEDIUM_BOOST_KEYWORDS = [
    "loss", "impairment", "write-off", "restructur", "layoff",
    "closure", "shutdown", "delist",
]

# Portfolio weight (mock, from portfolio.json)
_PORT_WEIGHTS: dict[str, float] | None = None


def _load_weights() -> dict[str, float]:
    global _PORT_WEIGHTS
    if _PORT_WEIGHTS is not None:
        return _PORT_WEIGHTS
    try:
        import json
        from backend.config import PORTFOLIO_PATH
        data = json.loads(PORTFOLIO_PATH.read_text())
        _PORT_WEIGHTS = {h["symbol"]: h.get("weight", 1.0) for h in data["holdings"]}
    except Exception:
        _PORT_WEIGHTS = {t: 1.0 for t in PORTFOLIO_TICKERS}
    return _PORT_WEIGHTS


def _get_volume_ratio(symbol: str, date_str: str, conn: sqlite3.Connection) -> float | None:
    """Today's volume / 20-day average. Returns None if data missing."""
    try:
        rows = conn.execute(
            """SELECT volume, date FROM prices WHERE symbol=? AND date<=?
               ORDER BY date DESC LIMIT 21""",
            (symbol, date_str)
        ).fetchall()
        if len(rows) < 2:
            return None
        today_vol = rows[0]["volume"]
        avg_vol = mean(r["volume"] for r in rows[1:])
        return today_vol / avg_vol if avg_vol > 0 else None
    except Exception:
        return None


def _get_price_move(symbol: str, date_str: str, conn: sqlite3.Connection) -> float | None:
    """Day % change on announcement date."""
    try:
        rows = conn.execute(
            """SELECT close FROM prices WHERE symbol=? AND date<=?
               ORDER BY date DESC LIMIT 2""",
            (symbol, date_str)
        ).fetchall()
        if len(rows) < 2:
            return None
        return (rows[0]["close"] - rows[1]["close"]) / rows[1]["close"] * 100
    except Exception:
        return None


def _tier(score: float) -> str:
    if score >= 9.5:
        return "must_see"
    if score >= 4.0:
        return "worth_a_look"
    return "routine"


def score_announcement(
    ann_id: str,
    symbol: str,
    subject: str,
    category: str,
    ts: str,
    conn: sqlite3.Connection,
) -> tuple[float, str, str]:
    """Returns (score, tier, breakdown_json)."""
    weights = _load_weights()
    breakdown = {}

    base = CATEGORY_BASE.get(category, 0.5)
    breakdown["category_base"] = base

    # Portfolio weight boost
    port_weight = weights.get(symbol, 0.0)
    weight_mult = 1.0 + (port_weight * 0.5)  # 0–50% boost
    breakdown["portfolio_weight"] = port_weight
    breakdown["weight_mult"] = round(weight_mult, 3)

    # Keyword boost
    text = subject.lower()
    kw_boost = 0.0
    for kw in HIGH_BOOST_KEYWORDS:
        if kw in text:
            kw_boost = max(kw_boost, 3.0)
            breakdown.setdefault("keyword_hits", []).append(kw)
    for kw in MEDIUM_BOOST_KEYWORDS:
        if kw in text:
            kw_boost = max(kw_boost, 1.5)
            breakdown.setdefault("keyword_hits", []).append(kw)
    breakdown["keyword_boost"] = kw_boost

    # Management change boost: CEO/CFO/MD much higher
    mgmt_boost = 0.0
    if category == "management_change":
        senior = any(k in text for k in ["ceo", "cfo", "md", "managing director", "chief executive", "chief financial"])
        mgmt_boost = 2.0 if senior else 0.0
        breakdown["mgmt_senior_boost"] = mgmt_boost

    # Credit rating: downgrade extra boost
    rating_boost = 0.0
    if category == "credit_rating":
        if "downgrade" in text or "negative" in text or "watch" in text:
            rating_boost = 3.0
            breakdown["rating_downgrade_boost"] = rating_boost

    # Anomaly boost: volume or price spike on announcement date
    ann_date = ts[:10] if ts else ""
    anomaly_boost = 0.0
    anomaly_note = ""
    if ann_date:
        vol_ratio = _get_volume_ratio(symbol, ann_date, conn)
        price_move = _get_price_move(symbol, ann_date, conn)
        if vol_ratio and vol_ratio >= 2.0:
            anomaly_boost = min(3.0, vol_ratio * 0.8)
            anomaly_note += f"volume {vol_ratio:.1f}x "
        if price_move and abs(price_move) >= 5.0:
            anomaly_boost = max(anomaly_boost, 2.0)
            anomaly_note += f"price {price_move:+.1f}% "
        breakdown["vol_ratio"] = round(vol_ratio, 2) if vol_ratio else None
        breakdown["price_move_pct"] = round(price_move, 2) if price_move else None
        breakdown["anomaly_boost"] = round(anomaly_boost, 2)
        breakdown["anomaly_note"] = anomaly_note.strip()

    score = (base + kw_boost + mgmt_boost + rating_boost + anomaly_boost) * weight_mult
    score = round(score, 3)
    breakdown["final_score"] = score

    tier = _tier(score)
    return score, tier, json.dumps(breakdown)


def run_score_all():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, symbol, subject, category, ts FROM announcements WHERE category IS NOT NULL"
    ).fetchall()

    for row in rows:
        score, tier, breakdown = score_announcement(
            row["id"], row["symbol"], row["subject"] or "",
            row["category"], row["ts"], conn
        )
        conn.execute(
            "UPDATE announcements SET priority_score=?, priority_tier=?, score_breakdown=? WHERE id=?",
            (score, tier, breakdown, row["id"])
        )

    conn.commit()
    conn.close()
    print(f"Scored {len(rows)} announcements")
