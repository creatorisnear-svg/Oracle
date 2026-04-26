"""
Signal persistence — keeps the dashboard signal "sticky" once a directional
trade plan has been issued.

Without this, the system flip-flops on every refresh: agents disagree slightly
between calls and a CALL becomes HOLD becomes PUT, even though no real reversal
happened. Traders treat each Analyze click as "should I act?" — they need the
system to commit to a trade plan and stay with it until something material
changes.

Rules:
  • Same direction (CALL→CALL or PUT→PUT)  → keep original entry/target/stop,
    refresh confidence and tally only.
  • New went HOLD                           → keep the active directional
    trade. Agents going neutral isn't a reason to abandon an open position.
  • Opposite direction with weak consensus  → keep active trade.
  • Opposite direction with STRONG (≥8/12)  → flip. A genuine reversal.

The "active" trade for (symbol, horizon) is the most recent non-HOLD prediction
in predictions.db that:
  • is still pending (outcome IS NULL — verify_outcomes hasn't closed it), AND
  • is still within its hold window (created_at + horizon_window > now)

When verify_outcomes hits target/stop, it sets outcome → the trade becomes
inactive automatically. When the hold window elapses, it ages out the same way.
"""

from __future__ import annotations
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

from learning import DB_PATH, HORIZON_WINDOW_HOURS, _parse_iso

# Strong-reversal threshold — opposite side must reach this many votes
# (out of 10) before we abandon an open trade and flip directions.
# Bumped 6→7 with the v6.7 addition of MLAgent (10th agent) so the
# "STRONG" reversal bar stays meaningfully above the firing threshold
# of 7/12 (~58%). 8/12 ≈ 67% — true majority disagreement before a flip (v7.0).
STRONG_REVERSAL_VOTES = 17  # v7.1: scaled from 8/12 → 17/30 (~57%) for the 30-agent committee


def get_active_trade(symbol: str, horizon: str) -> Optional[dict]:
    """
    Return the active directional trade for (symbol, horizon), or None.
    Active = newest non-HOLD prediction that is still pending and inside
    its hold window.
    """
    horizon = (horizon or "swing").lower()
    window_h = HORIZON_WINDOW_HOURS.get(horizon, 24 * 5)
    earliest_still_open = (
        datetime.now(timezone.utc) - timedelta(hours=window_h)
    ).isoformat()

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, signal, confidence, entry_price, target_price,
                   stop_loss, created_at
            FROM predictions
            WHERE symbol = ?
              AND horizon = ?
              AND outcome IS NULL
              AND signal IN ('BUY_CALL', 'BUY_PUT')
              AND created_at >= ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol, horizon, earliest_still_open),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None
    return {
        "id": row["id"],
        "signal": row["signal"],
        "confidence": row["confidence"],
        "entry": row["entry_price"],
        "target": row["target_price"],
        "stop": row["stop_loss"],
        "created_at": row["created_at"],
    }


def _age_label(created_at: str) -> str:
    """Human-readable age string like '2h ago' or '3d ago'."""
    try:
        dt = _parse_iso(created_at)
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ""


def apply_persistence(judgment: dict, active: Optional[dict]) -> tuple[dict, bool]:
    """
    Decide whether to override the new judgment with the active trade plan.

    Returns (final_judgment, should_save_new_prediction).

    should_save_new_prediction is False when we're just confirming/holding
    the active trade — avoids polluting the predictions table with redundant
    snapshots of the same logical trade. It's True when the signal materially
    changed (no active trade, or strong reversal triggered a flip).
    """
    if not active:
        # No active trade — new judgment fires fresh. Save it.
        return judgment, True

    new_sig = judgment["signal"]
    active_sig = active["signal"]
    new_consensus = judgment.get("vote_tally", {}).get(new_sig, 0)
    age = _age_label(active["created_at"])

    # ── Same direction → confirmation. Keep original trade plan. ──
    if new_sig == active_sig:
        out = dict(judgment)
        out["entry_price"] = active["entry"]
        out["target_price"] = active["target"]
        out["stop_loss"] = active["stop"]
        out["sticky"] = {
            "kind": "confirmed",
            "open_since": active["created_at"],
            "age": age,
            "message": f"Locked in since {age} — agents reconfirmed",
        }
        # Don't save a new row; we're just refreshing the same trade
        return out, False

    # ── New went HOLD while we have an open trade → keep the trade. ──
    if new_sig == "HOLD":
        out = dict(judgment)
        out["signal"] = active_sig
        out["entry_price"] = active["entry"]
        out["target_price"] = active["target"]
        out["stop_loss"] = active["stop"]
        out["confidence"] = max(active["confidence"] * 0.85, 50.0)
        out["sticky"] = {
            "kind": "held",
            "open_since": active["created_at"],
            "age": age,
            "message": f"Locked in since {age} — agents mixed but staying with open trade",
        }
        return out, False

    # ── Opposite direction. Only flip on STRONG reversal consensus. ──
    if new_consensus >= STRONG_REVERSAL_VOTES:
        out = dict(judgment)
        out["sticky"] = {
            "kind": "flipped",
            "from": active_sig,
            "message": f"Strong reversal ({new_consensus}/12) — flipped from {active_sig}",
        }
        return out, True  # save the new flipped signal

    # ── Weak opposite → keep active. ──
    out = dict(judgment)
    out["signal"] = active_sig
    out["entry_price"] = active["entry"]
    out["target_price"] = active["target"]
    out["stop_loss"] = active["stop"]
    out["confidence"] = max(active["confidence"] * 0.70, 50.0)
    new_dir = "calls" if new_sig == "BUY_CALL" else "puts"
    out["sticky"] = {
        "kind": "held_against",
        "open_since": active["created_at"],
        "age": age,
        "message": (
            f"Locked in since {age} — only {new_consensus}/12 {new_dir} "
            f"(need {STRONG_REVERSAL_VOTES} to flip)"
        ),
    }
    return out, False
