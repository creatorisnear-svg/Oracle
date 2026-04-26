"""
Agent Learning System v5
Per-agent accuracy tracking with CALL/PUT breakdown.
Weights auto-adjust via Bayesian smoothing after outcomes are verified.
12 agents + Judge, SQLite-backed, survives restarts.

v5 closes the learning loop properly:
  • per-horizon maturity windows (intraday=2h, day=6h, swing=5d, position=14d)
  • true target-hit-before-stop verification using OHLC history
  • independent per-agent grading (each agent judged on its own vote vs
    the actual price action, NOT on agreement with the system signal)
"""
import sqlite3
import json
import os
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


# Per-horizon hold windows — how long after entry we judge an outcome.
# Tuned to each horizon's intended hold time (matches the HORIZONS dict
# in agents.py: intraday ≈1-2h, day ≈ rest of session, swing 1-5d, position 1-3w).
HORIZON_WINDOW_HOURS = {
    "intraday": 2,
    "day": 6,
    "swing": 24 * 5,
    "position": 24 * 14,
}
# Bar interval to fetch from yfinance for in-window high/low scanning.
HORIZON_FETCH_INTERVAL = {
    "intraday": "5m",
    "day": "15m",
    "swing": "1h",
    "position": "1d",
}
# How big a move needs to be before we say it's a "real" directional move
# when grading individual agents. Below this, the move is noise and HOLD wins.
# MUST scale with horizon — for a 14-day position window, 0.5% is below noise
# (almost any stock has a 0.5% excursion intraday), which previously made
# HOLD votes systematically grade WRONG on swing/position. The thresholds
# below are ~ATR-equivalent for a typical liquid US large-cap at each horizon.
AGENT_GRADE_NOISE_BY_HORIZON = {
    "intraday": 0.003,   # 0.30% — 5-min noise
    "day":      0.005,   # 0.50% — same-session move
    "swing":    0.015,   # 1.50% — multi-day excursion
    "position": 0.030,   # 3.00% — multi-week excursion
}
# Back-compat constant — used as a fallback when horizon is unknown.
AGENT_GRADE_NOISE_PCT = 0.005


def _noise_threshold(horizon: str) -> float:
    """Return the per-horizon flat-zone threshold used to decide if a move
    was 'real' (up/down) versus 'flat' for grading purposes."""
    return AGENT_GRADE_NOISE_BY_HORIZON.get(
        (horizon or "swing").lower(), AGENT_GRADE_NOISE_PCT
    )

DB_PATH = os.path.join(os.path.dirname(__file__), "predictions.db")

INITIAL_WEIGHTS = {
    "Price Action Agent": 1.0,
    "Technical Agent": 1.05,    # Slightly higher — multi-indicator
    "Volume Agent": 1.0,
    "Sentiment Agent": 0.80,    # Lower — news NLP is noisy
    "Options Flow Agent": 1.10, # Higher — direct market positioning data
    "Momentum Agent": 1.0,
    "Risk Agent": 1.0,
    "Fear & Greed Agent": 0.90, # Contrarian/macro
    "Political Agent": 0.75,    # Macro news is noisiest
}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT NOT NULL,
            signal       TEXT NOT NULL,
            confidence   REAL,
            entry_price  REAL,
            stop_loss    REAL,
            target_price REAL,
            agent_votes  TEXT,
            created_at   TEXT NOT NULL,
            outcome      TEXT,
            outcome_price REAL,
            outcome_checked_at TEXT,
            was_correct  INTEGER
        );

        CREATE TABLE IF NOT EXISTS agent_performance (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name  TEXT NOT NULL,
            symbol      TEXT,
            vote        TEXT,
            system_signal TEXT,
            confidence  REAL,
            was_correct INTEGER,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_weights (
            agent_name          TEXT PRIMARY KEY,
            weight              REAL NOT NULL DEFAULT 1.0,
            total_predictions   INTEGER DEFAULT 0,
            correct_predictions INTEGER DEFAULT 0,
            call_total          INTEGER DEFAULT 0,
            call_correct        INTEGER DEFAULT 0,
            put_total           INTEGER DEFAULT 0,
            put_correct         INTEGER DEFAULT 0,
            accuracy            REAL DEFAULT 0.5,
            updated_at          TEXT
        );
    """)

    # Migration: add new columns to existing agent_weights table if missing
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(agent_weights)").fetchall()}
    for col, typedef in [
        ("call_total", "INTEGER DEFAULT 0"),
        ("call_correct", "INTEGER DEFAULT 0"),
        ("put_total", "INTEGER DEFAULT 0"),
        ("put_correct", "INTEGER DEFAULT 0"),
    ]:
        if col not in existing_cols:
            c.execute(f"ALTER TABLE agent_weights ADD COLUMN {col} {typedef}")

    # Migration: add system_signal + prediction_id cols to agent_performance if missing
    ap_cols = {row[1] for row in c.execute("PRAGMA table_info(agent_performance)").fetchall()}
    if "system_signal" not in ap_cols:
        c.execute("ALTER TABLE agent_performance ADD COLUMN system_signal TEXT")
    if "prediction_id" not in ap_cols:
        c.execute("ALTER TABLE agent_performance ADD COLUMN prediction_id INTEGER")

    # Migration: add horizon column to predictions so verify_outcomes knows
    # which maturity window to apply. Old rows default to 'swing' (most common).
    pred_cols = {row[1] for row in c.execute("PRAGMA table_info(predictions)").fetchall()}
    if "horizon" not in pred_cols:
        c.execute("ALTER TABLE predictions ADD COLUMN horizon TEXT DEFAULT 'swing'")
        c.execute("UPDATE predictions SET horizon = 'swing' WHERE horizon IS NULL")

    for agent, weight in INITIAL_WEIGHTS.items():
        c.execute("""
            INSERT OR IGNORE INTO agent_weights
            (agent_name, weight, total_predictions, correct_predictions,
             call_total, call_correct, put_total, put_correct, accuracy, updated_at)
            VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0.5, ?)
        """, (agent, weight, datetime.now(timezone.utc).isoformat()))

    conn.commit()
    conn.close()


def save_prediction(symbol: str, signal: str, confidence: float,
                    entry_price: float, target_price: float, stop_loss: float,
                    agent_votes: dict, horizon: str = "swing") -> int:
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("""
        INSERT INTO predictions
        (symbol, signal, confidence, entry_price, stop_loss, target_price,
         agent_votes, created_at, horizon)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, signal, confidence, entry_price, stop_loss, target_price,
          json.dumps(agent_votes), now, horizon))
    pred_id = c.lastrowid
    conn.commit()
    conn.close()
    return pred_id


def _parse_iso(s: str) -> datetime:
    """Parse stored ISO timestamp back to a tz-aware datetime."""
    try:
        # fromisoformat handles both '+00:00' and naive strings
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _fetch_window_ohlc(symbol: str, start: datetime, end: datetime,
                       horizon: str):
    """
    Pull OHLC bars from yfinance covering [start, end] at the appropriate
    interval for the horizon. Returns DataFrame or None on failure.
    Includes pre/post market data so predictions made near close still
    have bars available.
    """
    try:
        # Suppress yfinance's own ERROR-level "possibly delisted" log noise
        # (which is misleading — it just means no bars in the requested
        # window, often a non-trading day). We handle the empty case below.
        import logging as _logging
        yf_log = _logging.getLogger("yfinance")
        prev_level = yf_log.level
        yf_log.setLevel(_logging.CRITICAL)
        try:
            import yfinance as yf
            interval = HORIZON_FETCH_INTERVAL.get(horizon, "1h")
            df = yf.download(
                symbol,
                start=start - timedelta(minutes=5),
                end=end + timedelta(hours=1),
                interval=interval,
                progress=False,
                auto_adjust=False,
                threads=False,
                prepost=True,    # include after-hours bars
            )
        finally:
            yf_log.setLevel(prev_level)

        if df is None or df.empty:
            return None
        # yfinance returns multi-index columns when downloading single symbol on
        # some versions — flatten if needed
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df
    except Exception as e:
        logger.debug(f"_fetch_window_ohlc({symbol}, {horizon}): {e}")
        return None


def _resolve_target_vs_stop(df, entry: float, target: float, stop: float,
                            signal: str, horizon: str = "swing") -> tuple[str, float]:
    """
    Walk OHLC bars in chronological order and determine which level was hit
    first — target or stop. Returns (outcome, exit_price).

    Same-bar ambiguity rule: when both target and stop are hit inside the
    SAME bar, we use the bar's open to break the tie. If the bar opened
    closer to the target than the stop, target was probably hit first
    (the bar gapped/moved through it). Otherwise stop wins (conservative).

    outcome ∈ {"TARGET_HIT", "STOP_HIT", "TIMEOUT_UP", "TIMEOUT_DOWN", "TIMEOUT_FLAT"}
    """
    if df is None or len(df) == 0:
        return ("TIMEOUT_FLAT", entry)

    last_close = float(df["Close"].iloc[-1])
    has_open = "Open" in df.columns

    for _, bar in df.iterrows():
        hi = float(bar["High"])
        lo = float(bar["Low"])
        op = float(bar["Open"]) if has_open else (hi + lo) / 2

        if signal == "BUY_CALL":
            target_hit = hi >= target
            stop_hit = lo <= stop
            if target_hit and stop_hit:
                # Same-bar ambiguity: use OPEN to decide which was hit first.
                # If the bar OPENED already past the target, target was clearly
                # taken first; opened past the stop → stop first; otherwise
                # whichever was closer to the open.
                if op >= target:
                    return ("TARGET_HIT", target)
                if op <= stop:
                    return ("STOP_HIT", stop)
                if abs(op - target) < abs(op - stop):
                    return ("TARGET_HIT", target)
                return ("STOP_HIT", stop)
            if target_hit:
                return ("TARGET_HIT", target)
            if stop_hit:
                return ("STOP_HIT", stop)
        elif signal == "BUY_PUT":
            target_hit = lo <= target  # PUT target is below entry
            stop_hit = hi >= stop      # PUT stop is above entry
            if target_hit and stop_hit:
                if op <= target:
                    return ("TARGET_HIT", target)
                if op >= stop:
                    return ("STOP_HIT", stop)
                if abs(op - target) < abs(op - stop):
                    return ("TARGET_HIT", target)
                return ("STOP_HIT", stop)
            if target_hit:
                return ("TARGET_HIT", target)
            if stop_hit:
                return ("STOP_HIT", stop)
        # HOLD: not a directional trade — no target/stop tracking
    # No level hit — categorize by net direction (horizon-aware noise floor)
    pct = (last_close - entry) / entry if entry else 0
    if abs(pct) < _noise_threshold(horizon):
        return ("TIMEOUT_FLAT", last_close)
    return (("TIMEOUT_UP" if pct > 0 else "TIMEOUT_DOWN"), last_close)


def _grade_agent_vote(agent_vote: str, actual_dir: str) -> int:
    """
    Grade an individual agent's vote against the actual price direction
    during the hold window — INDEPENDENT of whether the system fired.
    actual_dir ∈ {"up", "down", "flat"}.
    """
    if actual_dir == "up":
        return 1 if agent_vote == "BUY_CALL" else 0
    if actual_dir == "down":
        return 1 if agent_vote == "BUY_PUT" else 0
    return 1 if agent_vote == "HOLD" else 0


def _resolve_one(c: sqlite3.Cursor, row, now_iso: str) -> bool:
    """Resolve a single matured prediction row. Returns True if resolved."""
    pred_id = row["id"]
    symbol = row["symbol"]
    signal = row["signal"]
    entry = row["entry_price"] or 0
    target = row["target_price"] or entry
    stop = row["stop_loss"] or entry
    horizon = (row["horizon"] or "swing").lower()
    created_at = _parse_iso(row["created_at"])
    window_h = HORIZON_WINDOW_HOURS.get(horizon, 24 * 5)
    end_at = created_at + timedelta(hours=window_h)

    # Pull OHLC across the actual hold window
    df = _fetch_window_ohlc(symbol, created_at, end_at, horizon)

    if df is None or len(df) == 0:
        # Couldn't fetch OHLC. If the window ended >7 days ago we'll
        # almost certainly never get the data (non-trading-day window,
        # data outside yfinance's intraday retention, etc.) — mark stale
        # so it stops getting retried on every verifier pass.
        if end_at + timedelta(days=7) < datetime.now(timezone.utc):
            c.execute("""
                UPDATE predictions
                SET outcome = 'STALE', outcome_checked_at = ?
                WHERE id = ?
            """, (now_iso, pred_id))
            return False  # marked but not graded
        return False  # leave pending, retry next pass

    last_close = float(df["Close"].iloc[-1])
    high = float(df["High"].max())
    low = float(df["Low"].min())

    # Determine actual directional outcome for agent grading.
    # We use the highest/lowest excursion inside the window (not the close)
    # because options pay off the BEST move during the trade, not just the
    # net drift from open to close. Noise floor is HORIZON-AWARE — for a
    # 14-day position window 0.5% is below noise so HOLD votes were
    # systematically graded WRONG before this fix.
    noise = _noise_threshold(horizon)
    pct_high = (high - entry) / entry if entry else 0
    pct_low = (low - entry) / entry if entry else 0
    if pct_high >= noise and pct_high > abs(pct_low):
        actual_dir = "up"
    elif pct_low <= -noise and abs(pct_low) > pct_high:
        actual_dir = "down"
    else:
        actual_dir = "flat"

    # Resolve the system signal (target vs stop)
    if signal == "HOLD":
        # HOLD is correct if neither a meaningful up nor down move materialized
        correct = int(actual_dir == "flat")
        outcome_label = "CORRECT" if correct else "WRONG"
        outcome_price = last_close
    else:
        outcome_kind, outcome_price = _resolve_target_vs_stop(
            df, entry, target, stop, signal, horizon
        )
        if outcome_kind == "TARGET_HIT":
            correct = 1
            outcome_label = "CORRECT"
        elif outcome_kind == "STOP_HIT":
            correct = 0
            outcome_label = "WRONG"
        else:
            # Timeout: judge by net direction
            if signal == "BUY_CALL":
                correct = int(outcome_kind == "TIMEOUT_UP")
            else:  # BUY_PUT
                correct = int(outcome_kind == "TIMEOUT_DOWN")
            outcome_label = "CORRECT" if correct else "WRONG"

    c.execute("""
        UPDATE predictions
        SET outcome = ?, outcome_price = ?, outcome_checked_at = ?, was_correct = ?
        WHERE id = ?
    """, (outcome_label, outcome_price, now_iso, correct, pred_id))

    # Per-agent grading — each agent judged INDEPENDENTLY against actual move
    try:
        votes = json.loads(row["agent_votes"] or "{}")
        for agent_name, agent_vote in votes.items():
            agent_correct = _grade_agent_vote(agent_vote, actual_dir)
            c.execute("""
                INSERT INTO agent_performance
                (agent_name, symbol, vote, system_signal, confidence,
                 was_correct, created_at, prediction_id)
                VALUES (?, ?, ?, ?, 70, ?, ?, ?)
            """, (agent_name, symbol, agent_vote, signal, agent_correct,
                  now_iso, pred_id))
    except Exception as e:
        logger.warning(f"Agent perf insert pred={pred_id}: {e}")

    return True


def _matured_pending_query(c: sqlite3.Cursor, symbol: str | None = None):
    """Yield pending prediction rows that have passed their maturity window."""
    now = datetime.now(timezone.utc)
    where = "outcome IS NULL"
    args: list = []
    if symbol:
        where += " AND symbol = ?"
        args.append(symbol)
    rows = c.execute(f"""
        SELECT id, symbol, signal, entry_price, target_price, stop_loss,
               horizon, created_at, agent_votes
        FROM predictions
        WHERE {where}
        ORDER BY created_at ASC
        LIMIT 200
    """, args).fetchall()

    for r in rows:
        horizon = (r["horizon"] or "swing").lower()
        window_h = HORIZON_WINDOW_HOURS.get(horizon, 24 * 5)
        created_at = _parse_iso(r["created_at"])
        if created_at + timedelta(hours=window_h) <= now:
            yield r


def verify_outcomes(symbol: str | None = None,
                    current_price: float | None = None) -> dict:
    """
    Resolve every pending prediction whose hold window has elapsed.
    For each matured prediction we pull real OHLC bars over the hold
    window and decide whether the target was hit before the stop.
    Each agent is then graded INDEPENDENTLY against the actual price
    direction (not against agreement with the system signal).

    `symbol`: restrict to one symbol; None = scan everything.
    `current_price`: ignored (kept for backwards-compat with old callers).
    Returns: {"resolved": int, "skipped": int, "scanned": int}.
    """
    conn = get_conn()
    c = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()

    resolved = scanned = 0
    for row in _matured_pending_query(c, symbol):
        scanned += 1
        try:
            if _resolve_one(c, row, now_iso):
                resolved += 1
        except Exception as e:
            logger.warning(f"_resolve_one pred={row['id']}: {e}")

    if resolved > 0:
        conn.commit()
        _recalculate_weights(c)
        conn.commit()
        # v6.7 — let the ML Agent retrain on the freshly-resolved data so it
        # incorporates the new ground-truth labels into its weight vector.
        # Best-effort: failures here must never break the verification loop.
        try:
            from agents import MLAgent
            stats = MLAgent.train_from_resolved(conn)
            if stats.get("trained"):
                logger.info(f"MLAgent retrained: n={stats['samples']}, loss={stats.get('loss')}")
        except Exception as e:
            logger.warning(f"MLAgent retrain skipped: {e}")
    conn.close()
    return {"resolved": resolved, "skipped": scanned - resolved, "scanned": scanned}


def _recalculate_weights(c: sqlite3.Cursor):
    agents = c.execute("SELECT DISTINCT agent_name FROM agent_performance").fetchall()
    for row in agents:
        agent = row["agent_name"]

        stats = c.execute("""
            SELECT
                COUNT(*) as total,
                SUM(was_correct) as correct,
                SUM(CASE WHEN vote='BUY_CALL' THEN 1 ELSE 0 END) as call_total,
                SUM(CASE WHEN vote='BUY_CALL' AND was_correct=1 THEN 1 ELSE 0 END) as call_correct,
                SUM(CASE WHEN vote='BUY_PUT' THEN 1 ELSE 0 END) as put_total,
                SUM(CASE WHEN vote='BUY_PUT' AND was_correct=1 THEN 1 ELSE 0 END) as put_correct
            FROM agent_performance WHERE agent_name = ?
        """, (agent,)).fetchone()

        total = stats["total"] or 0
        correct = stats["correct"] or 0

        if total >= 5:
            accuracy = (correct + 1) / (total + 2)
            weight = round(0.5 + accuracy, 4)
        else:
            accuracy = 0.5
            weight = INITIAL_WEIGHTS.get(agent, 1.0)

        c.execute("""
            INSERT INTO agent_weights
            (agent_name, weight, total_predictions, correct_predictions,
             call_total, call_correct, put_total, put_correct, accuracy, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_name) DO UPDATE SET
                weight=excluded.weight,
                total_predictions=excluded.total_predictions,
                correct_predictions=excluded.correct_predictions,
                call_total=excluded.call_total,
                call_correct=excluded.call_correct,
                put_total=excluded.put_total,
                put_correct=excluded.put_correct,
                accuracy=excluded.accuracy,
                updated_at=excluded.updated_at
        """, (agent, weight, total, correct,
              stats["call_total"] or 0, stats["call_correct"] or 0,
              stats["put_total"] or 0, stats["put_correct"] or 0,
              round(accuracy, 4), datetime.now(timezone.utc).isoformat()))


def get_agent_weights() -> dict:
    conn = get_conn()
    rows = conn.execute("""
        SELECT agent_name, weight, accuracy, total_predictions,
               correct_predictions, call_total, call_correct,
               put_total, put_correct
        FROM agent_weights
    """).fetchall()
    conn.close()
    return {
        r["agent_name"]: {
            "weight": r["weight"],
            "accuracy": r["accuracy"],
            "total": r["total_predictions"],
            "correct": r["correct_predictions"],
            "call_total": r["call_total"] or 0,
            "call_correct": r["call_correct"] or 0,
            "put_total": r["put_total"] or 0,
            "put_correct": r["put_correct"] or 0,
        }
        for r in rows
    }


def get_accuracy_stats() -> dict:
    conn = get_conn()
    total = conn.execute(
        "SELECT COUNT(*) as n FROM predictions WHERE outcome IS NOT NULL"
    ).fetchone()["n"]
    correct = conn.execute(
        "SELECT COUNT(*) as n FROM predictions WHERE was_correct = 1"
    ).fetchone()["n"]
    by_signal = conn.execute("""
        SELECT signal, COUNT(*) as total, SUM(was_correct) as correct
        FROM predictions WHERE outcome IS NOT NULL
        GROUP BY signal
    """).fetchall()
    conn.close()

    acc = correct / total if total > 0 else 0.0
    breakdown = {
        r["signal"]: {"total": r["total"], "correct": r["correct"] or 0}
        for r in by_signal
    }
    return {
        "total": total, "correct": correct,
        "accuracy": round(acc, 4), "by_signal": breakdown
    }


def get_weight_history(limit: int = 30) -> list:
    """Return the most recent agent weight-change events.

    Each row in `agent_performance` is one resolved vote. Walking those rows
    chronologically and re-applying the same Bayesian-smoothing formula that
    `_recalculate_weights` uses gives us the weight value before AND after
    each event — so the UI can show a literal feed of the AI learning.

    The formula has a "warm-up" floor: agents with <5 graded votes stay at
    their initial weight (1.0). We surface that as `phase: "warmup"` so the
    UI can show "still learning" instead of a deceptive 0.00 delta.
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, agent_name, symbol, vote, system_signal, was_correct, created_at
        FROM agent_performance
        ORDER BY id ASC
    """).fetchall()
    conn.close()

    # Cumulative per-agent stats as we replay history forward
    running: dict[str, dict[str, int]] = {}
    events: list[dict] = []

    def _weight(agent: str, total: int, correct: int) -> tuple[float, str]:
        if total >= 5:
            accuracy = (correct + 1) / (total + 2)
            return round(0.5 + accuracy, 4), "active"
        return round(INITIAL_WEIGHTS.get(agent, 1.0), 4), "warmup"

    for r in rows:
        agent = r["agent_name"]
        st = running.setdefault(agent, {"total": 0, "correct": 0})
        before_total, before_correct = st["total"], st["correct"]
        w_before, phase_before = _weight(agent, before_total, before_correct)

        st["total"] += 1
        st["correct"] += int(r["was_correct"] or 0)
        after_total, after_correct = st["total"], st["correct"]
        w_after, phase_after = _weight(agent, after_total, after_correct)

        events.append({
            "id": r["id"],
            "agent": agent,
            "symbol": r["symbol"],
            "vote": r["vote"],
            "system_signal": r["system_signal"],
            "was_correct": int(r["was_correct"] or 0),
            "created_at": r["created_at"],
            "weight_before": w_before,
            "weight_after": w_after,
            "delta": round(w_after - w_before, 4),
            "phase": phase_after,
            "phase_before": phase_before,
            "total_after": after_total,
            "correct_after": after_correct,
        })

    # Newest first, capped to `limit`
    events.reverse()
    return events[:limit]


def get_horizon_multipliers(min_samples: int = 10) -> dict:
    """Per-horizon agent calibration multipliers.

    Same architectural pattern as `meta_learning.get_regime_multipliers()`
    and `get_symbol_multipliers()` — defaults to 1.0 (no effect) and only
    activates once enough horizon-specific samples have accumulated, then
    applies a bounded multiplier in [0.7, 1.3].

    Why this matters: an agent's edge is HORIZON-DEPENDENT.
    • SentimentAgent reads news headlines. News takes hours-to-days to
      play out → great for swing/position, useless for 5-min intraday.
    • MomentumAgent reads ROC/breakouts. Strong on intraday/day where
      momentum carries within session → reverts on multi-week position.
    • RiskAgent reads ATR + regime → roughly horizon-symmetric.
    The current global agent_weights table averages all of this together,
    so a Sentiment agent that's 70% on swing and 40% on intraday gets
    the same mediocre weight everywhere. Per-horizon weighting separates
    those into two lanes, like the existing per-regime/per-symbol lanes.

    Returns: {(agent_name, horizon): multiplier}
    """
    out: dict = {}
    try:
        conn = get_conn()
        rows = conn.execute("""
            SELECT ap.agent_name, p.horizon,
                   COUNT(*) AS total,
                   SUM(ap.was_correct) AS correct
            FROM agent_performance ap
            JOIN predictions p ON p.id = ap.prediction_id
            WHERE p.horizon IS NOT NULL
              AND ap.was_correct IS NOT NULL
            GROUP BY ap.agent_name, p.horizon
        """).fetchall()
        conn.close()
        for r in rows:
            total = r["total"] or 0
            if total < min_samples:
                continue
            acc = (r["correct"] or 0) / total
            # Map 0.30..0.70 → 0.7..1.3 (same shape as the regime mapping
            # so users see consistent magnitudes in the UI).
            mult = 1.0 + (acc - 0.5) * 1.0
            mult = max(0.7, min(1.3, mult))
            out[(r["agent_name"], r["horizon"])] = round(mult, 3)
    except Exception as e:
        logger.warning(f"get_horizon_multipliers: {e}")
    return out


def get_recent_predictions(symbol: str, limit: int = 20) -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, symbol, signal, confidence, entry_price, stop_loss,
               target_price, created_at, outcome, outcome_price, was_correct
        FROM predictions
        WHERE symbol = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (symbol, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# LearningSystem — clean interface for server.py
# ─────────────────────────────────────────────────────────────────────────────
class LearningSystem:
    def __init__(self):
        init_db()

    def get_weights(self) -> dict:
        """Return {agent_name: weight_float}."""
        return {name: info["weight"] for name, info in get_agent_weights().items()}

    def get_horizon_multipliers(self, min_samples: int = 10) -> dict:
        """Per-(agent, horizon) calibration multipliers. See module docstring."""
        try:
            return get_horizon_multipliers(min_samples=min_samples)
        except Exception as e:
            logger.warning(f"LearningSystem.get_horizon_multipliers: {e}")
            return {}

    def save_prediction(self, symbol: str, signal: str, confidence: float,
                        entry_price: float, target_price: float, stop_loss: float,
                        agent_votes: dict, horizon: str = "swing") -> int:
        return save_prediction(symbol, signal, confidence, entry_price,
                               target_price, stop_loss, agent_votes, horizon)

    def verify_outcomes(self, symbol: str, current_price: float | None = None) -> dict:
        try:
            return verify_outcomes(symbol, current_price)
        except Exception as e:
            logger.warning(f"verify_outcomes: {e}")
            return {"resolved": 0, "skipped": 0, "scanned": 0}

    def verify_all_pending(self) -> dict:
        """
        Resolve every matured pending prediction across all symbols.
        Used by the periodic background loop in server.py.
        """
        try:
            return verify_outcomes(symbol=None)
        except Exception as e:
            logger.warning(f"verify_all_pending: {e}")
            return {"resolved": 0, "skipped": 0, "scanned": 0}

    def get_accuracy_report(self) -> dict:
        """Returns the full accuracy report in the shape the frontend expects."""
        stats = get_accuracy_stats()
        weights = get_agent_weights()

        agents_list = []
        for name, info in weights.items():
            win_rate = info["accuracy"]
            agents_list.append({
                "agent": name,
                "total": info["total"],
                "correct": info["correct"],
                "win_rate": win_rate,
                "call_total": info["call_total"],
                "call_correct": info["call_correct"],
                "put_total": info["put_total"],
                "put_correct": info["put_correct"],
                "weight": info["weight"],
            })

        # Sort by win rate descending
        agents_list.sort(key=lambda x: x["win_rate"], reverse=True)

        return {
            "overall_win_rate": stats["accuracy"],
            "total_predictions": stats["total"],
            "correct_predictions": stats["correct"],
            "by_signal": stats["by_signal"],
            "agents": agents_list,
        }

    def get_history(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "history": get_recent_predictions(symbol),
        }

    def get_weight_history(self, limit: int = 30) -> list:
        """Recent agent weight-change events for the 'AI is learning' feed."""
        try:
            return get_weight_history(limit)
        except Exception as e:
            logger.warning(f"get_weight_history: {e}")
            return []
