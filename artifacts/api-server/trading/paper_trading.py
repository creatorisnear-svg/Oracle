"""
Paper Trading System
SQLite-backed simulated options trading using TradeSignal AI's predictions.
Tracks: starting balance, open positions, closed positions, realized + unrealized P/L.
Auto-closes positions when target or stop is hit, or when the horizon window expires.
"""
import os
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "predictions.db")
STARTING_BALANCE = 10_000.00


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = _conn()
    cur = c.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS paper_account (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            starting_balance REAL NOT NULL,
            cash            REAL NOT NULL,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paper_positions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            signal          TEXT NOT NULL,           -- BUY_CALL or BUY_PUT
            horizon         TEXT NOT NULL,
            entry_price     REAL NOT NULL,
            target_price    REAL NOT NULL,
            stop_loss       REAL NOT NULL,
            shares          REAL NOT NULL,           -- notional shares (margin allowed)
            cost            REAL NOT NULL,           -- cash committed
            confidence      REAL,
            opened_at       TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'open',  -- open | closed
            close_price     REAL,
            close_reason    TEXT,                    -- target | stop | manual | expired
            closed_at       TEXT,
            pnl             REAL,
            pnl_pct         REAL
        );
        CREATE INDEX IF NOT EXISTS ix_pos_status ON paper_positions(status);
        CREATE INDEX IF NOT EXISTS ix_pos_symbol ON paper_positions(symbol);
    """)
    # Seed account if missing
    row = cur.execute("SELECT id FROM paper_account WHERE id = 1").fetchone()
    if not row:
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(
            "INSERT INTO paper_account (id, starting_balance, cash, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)",
            (STARTING_BALANCE, STARTING_BALANCE, now, now),
        )
    c.commit()
    c.close()


def reset_account(starting: float = STARTING_BALANCE) -> dict:
    """Wipe positions and reset account balance. Used by the UI reset button."""
    c = _conn()
    cur = c.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute("DELETE FROM paper_positions")
    cur.execute(
        "UPDATE paper_account SET starting_balance = ?, cash = ?, updated_at = ? WHERE id = 1",
        (starting, starting, now),
    )
    c.commit()
    c.close()
    return get_account()


def get_account() -> dict:
    c = _conn()
    row = c.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
    c.close()
    if not row:
        return {"starting_balance": STARTING_BALANCE, "cash": STARTING_BALANCE}
    return dict(row)


def open_position(
    symbol: str,
    signal: str,
    horizon: str,
    entry_price: float,
    target_price: float,
    stop_loss: float,
    confidence: float,
    risk_pct: float = 0.10,    # use 10% of cash per trade by default
) -> dict:
    """Open a paper position based on a live signal.
    Sizing uses `risk_pct` of available cash (so the user can stack trades).
    """
    if signal not in ("BUY_CALL", "BUY_PUT"):
        return {"error": "signal must be BUY_CALL or BUY_PUT"}
    acct = get_account()
    cash = float(acct.get("cash") or 0)
    if cash < 50:
        return {"error": f"insufficient cash (${cash:.2f}) — reset the account"}

    cost = max(50.0, min(cash, cash * risk_pct))
    shares = round(cost / max(entry_price, 0.01), 4)
    cost = round(shares * entry_price, 2)

    now = datetime.now(timezone.utc).isoformat()
    c = _conn()
    cur = c.cursor()
    cur.execute(
        "INSERT INTO paper_positions "
        "(symbol, signal, horizon, entry_price, target_price, stop_loss, "
        " shares, cost, confidence, opened_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')",
        (symbol.upper(), signal, horizon, entry_price, target_price, stop_loss,
         shares, cost, confidence, now),
    )
    pos_id = cur.lastrowid
    cur.execute(
        "UPDATE paper_account SET cash = cash - ?, updated_at = ? WHERE id = 1",
        (cost, now),
    )
    c.commit()
    pos = dict(c.execute("SELECT * FROM paper_positions WHERE id = ?", (pos_id,)).fetchone())
    c.close()
    return pos


def _compute_pnl(pos: dict, current_price: float) -> tuple[float, float]:
    """Return (pnl, pnl_pct). Calls profit when price rises, puts when it falls."""
    entry = float(pos["entry_price"])
    shares = float(pos["shares"])
    if pos["signal"] == "BUY_CALL":
        pnl = (current_price - entry) * shares
    else:  # BUY_PUT
        pnl = (entry - current_price) * shares
    pnl_pct = (pnl / float(pos["cost"])) * 100 if pos["cost"] else 0.0
    return round(pnl, 2), round(pnl_pct, 2)


def close_position(position_id: int, current_price: float, reason: str = "manual") -> Optional[dict]:
    c = _conn()
    row = c.execute(
        "SELECT * FROM paper_positions WHERE id = ? AND status = 'open'",
        (position_id,),
    ).fetchone()
    if not row:
        c.close()
        return None
    pos = dict(row)
    pnl, pnl_pct = _compute_pnl(pos, current_price)
    proceeds = float(pos["cost"]) + pnl
    now = datetime.now(timezone.utc).isoformat()

    cur = c.cursor()
    cur.execute(
        "UPDATE paper_positions SET status='closed', close_price=?, "
        "close_reason=?, closed_at=?, pnl=?, pnl_pct=? WHERE id = ?",
        (current_price, reason, now, pnl, pnl_pct, position_id),
    )
    cur.execute(
        "UPDATE paper_account SET cash = cash + ?, updated_at = ? WHERE id = 1",
        (proceeds, now),
    )
    c.commit()
    closed = dict(c.execute("SELECT * FROM paper_positions WHERE id = ?", (position_id,)).fetchone())
    c.close()
    return closed


def list_positions(status: str = "open") -> list[dict]:
    c = _conn()
    if status == "all":
        rows = c.execute("SELECT * FROM paper_positions ORDER BY id DESC").fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM paper_positions WHERE status = ? ORDER BY id DESC",
            (status,),
        ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def mark_to_market(price_lookup) -> dict:
    """Annotate every open position with live current_price + unrealized P/L.
    `price_lookup(symbol) -> float | None` is injected by the server.
    Also auto-closes positions whose target/stop has been hit.
    """
    open_positions = list_positions("open")
    enriched: list[dict] = []
    auto_closed: list[dict] = []

    for pos in open_positions:
        sym = pos["symbol"]
        try:
            price = price_lookup(sym)
        except Exception:
            price = None
        if price is None:
            pos["current_price"] = None
            pos["unrealized_pnl"] = None
            pos["unrealized_pnl_pct"] = None
            enriched.append(pos)
            continue

        # Check auto-close conditions on target / stop
        hit = None
        if pos["signal"] == "BUY_CALL":
            if price >= pos["target_price"]:
                hit = "target"
            elif price <= pos["stop_loss"]:
                hit = "stop"
        else:  # BUY_PUT
            if price <= pos["target_price"]:
                hit = "target"
            elif price >= pos["stop_loss"]:
                hit = "stop"

        if hit:
            closed = close_position(pos["id"], price, hit)
            if closed:
                auto_closed.append(closed)
            continue

        pnl, pnl_pct = _compute_pnl(pos, price)
        pos["current_price"] = round(price, 2)
        pos["unrealized_pnl"] = pnl
        pos["unrealized_pnl_pct"] = pnl_pct
        enriched.append(pos)

    return {"open": enriched, "auto_closed": auto_closed}


def stats() -> dict:
    """Compute realized stats across closed positions."""
    c = _conn()
    rows = c.execute(
        "SELECT * FROM paper_positions WHERE status = 'closed'"
    ).fetchall()
    c.close()
    closed = [dict(r) for r in rows]
    total = len(closed)
    if total == 0:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "best": None, "worst": None,
            "by_horizon": {}, "by_signal": {},
        }
    wins = [r for r in closed if (r.get("pnl") or 0) > 0]
    losses = [r for r in closed if (r.get("pnl") or 0) <= 0]
    total_pnl = sum((r.get("pnl") or 0) for r in closed)
    by_h: dict = {}
    for r in closed:
        h = r.get("horizon", "?")
        by_h.setdefault(h, {"trades": 0, "wins": 0, "pnl": 0.0})
        by_h[h]["trades"] += 1
        by_h[h]["wins"] += 1 if (r.get("pnl") or 0) > 0 else 0
        by_h[h]["pnl"] += r.get("pnl") or 0
    for h, d in by_h.items():
        d["win_rate"] = round(d["wins"] / d["trades"] * 100, 1)
        d["pnl"] = round(d["pnl"], 2)

    by_sig: dict = {}
    for r in closed:
        s = r.get("signal", "?")
        by_sig.setdefault(s, {"trades": 0, "wins": 0, "pnl": 0.0})
        by_sig[s]["trades"] += 1
        by_sig[s]["wins"] += 1 if (r.get("pnl") or 0) > 0 else 0
        by_sig[s]["pnl"] += r.get("pnl") or 0
    for s, d in by_sig.items():
        d["win_rate"] = round(d["wins"] / d["trades"] * 100, 1)
        d["pnl"] = round(d["pnl"], 2)

    return {
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / total * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(sum(r["pnl"] for r in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(r["pnl"] for r in losses) / len(losses), 2) if losses else 0.0,
        "best": max(closed, key=lambda r: r.get("pnl") or 0),
        "worst": min(closed, key=lambda r: r.get("pnl") or 0),
        "by_horizon": by_h,
        "by_signal": by_sig,
    }


# Initialize on import
init_db()
