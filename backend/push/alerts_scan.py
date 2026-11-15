"""
Push — Alerts scan
Checks alert_rules against prices and announcements.
Idempotent via UNIQUE(rule_id, symbol, date, announcement_id).
"""
import json
import sqlite3
import datetime
from statistics import mean

from backend.config import DB_PATH


def _volume_ratio(symbol: str, date_str: str, conn: sqlite3.Connection) -> float | None:
    rows = conn.execute(
        "SELECT volume, date FROM prices WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 21",
        (symbol, date_str)
    ).fetchall()
    if len(rows) < 2:
        return None
    today = rows[0][0]
    avg = mean(r[0] for r in rows[1:])
    return today / avg if avg > 0 else None


def _price_move(symbol: str, date_str: str, conn: sqlite3.Connection) -> float | None:
    rows = conn.execute(
        "SELECT close FROM prices WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 2",
        (symbol, date_str)
    ).fetchall()
    if len(rows) < 2:
        return None
    return (rows[0][0] - rows[1][0]) / rows[1][0] * 100


def scan_alerts():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rules = conn.execute(
        "SELECT * FROM alert_rules WHERE enabled=1"
    ).fetchall()

    fired = 0
    now = datetime.datetime.utcnow().isoformat()

    # Get all price dates per symbol for the last 30 days
    cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    price_dates = conn.execute(
        "SELECT DISTINCT symbol, date FROM prices WHERE date>=? ORDER BY date",
        (cutoff,)
    ).fetchall()

    for rule in rules:
        rule_id = rule["id"]
        kind = rule["kind"]
        threshold = rule["threshold"]
        scope_symbol = rule["symbol"]  # None = global

        if kind == "volume_ratio":
            for pd in price_dates:
                symbol, date = pd["symbol"], pd["date"]
                if scope_symbol and scope_symbol != symbol:
                    continue
                ratio = _volume_ratio(symbol, date, conn)
                if ratio and ratio >= (threshold or 2.0):
                    msg = f"{symbol} volume {ratio:.1f}x avg on {date}"
                    try:
                        conn.execute(
                            """INSERT OR IGNORE INTO alert_events
                               (rule_id,symbol,date,announcement_id,kind,value,threshold,message,fired_at)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (rule_id, symbol, date, "", kind, round(ratio, 2), threshold, msg, now)
                        )
                        fired += 1
                    except Exception:
                        pass

        elif kind == "price_move":
            for pd in price_dates:
                symbol, date = pd["symbol"], pd["date"]
                if scope_symbol and scope_symbol != symbol:
                    continue
                move = _price_move(symbol, date, conn)
                if move and abs(move) >= (threshold or 5.0):
                    msg = f"{symbol} price {move:+.1f}% on {date}"
                    try:
                        conn.execute(
                            """INSERT OR IGNORE INTO alert_events
                               (rule_id,symbol,date,announcement_id,kind,value,threshold,message,fired_at)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (rule_id, symbol, date, "", kind, round(move, 2), threshold, msg, now)
                        )
                        fired += 1
                    except Exception:
                        pass

        elif kind == "announcement_category":
            cats = set((rule["categories"] or "").split(","))
            anns = conn.execute(
                "SELECT id, symbol, ts, category FROM announcements WHERE category IN ({})".format(
                    ",".join("?" * len(cats))
                ),
                list(cats)
            ).fetchall()
            for ann in anns:
                if scope_symbol and scope_symbol != ann["symbol"]:
                    continue
                date = ann["ts"][:10] if ann["ts"] else ""
                msg = f"{ann['symbol']} {ann['category']} announcement on {date}"
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO alert_events
                           (rule_id,symbol,date,announcement_id,kind,value,threshold,message,fired_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (rule_id, ann["symbol"], date, ann["id"], kind, 1.0, 0.0, msg, now)
                    )
                    fired += 1
                except Exception:
                    pass

    conn.commit()
    conn.close()
    print(f"Alert scan: {fired} events fired")
    return fired


if __name__ == "__main__":
    scan_alerts()
