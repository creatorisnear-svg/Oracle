"""
Agent Learning System v4
Per-agent accuracy tracking with CALL/PUT breakdown.
Weights auto-adjust via Bayesian smoothing after outcomes are verified.
9 agents + Judge, SQLite-backed, survives restarts.
"""
import sqlite3
import json
import os
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

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
                    agent_votes: dict) -> int:
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("""
        INSERT INTO predictions
        (symbol, signal, confidence, entry_price, stop_loss, target_price,
         agent_votes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, signal, confidence, entry_price, stop_loss, target_price,
          json.dumps(agent_votes), now))
    pred_id = c.lastrowid
    conn.commit()
    conn.close()
    return pred_id


def verify_outcomes(symbol: str, current_price: float):
    """
    Check predictions from 1+ days ago and mark them correct/wrong.
    BUY_CALL correct if price moved up ≥ 0.5% from entry.
    BUY_PUT  correct if price moved down ≥ 0.5% from entry.
    HOLD correct if price moved < 0.5%.
    """
    conn = get_conn()
    c = conn.cursor()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    rows = c.execute("""
        SELECT id, signal, entry_price, agent_votes
        FROM predictions
        WHERE symbol = ? AND outcome IS NULL AND created_at <= ?
    """, (symbol, cutoff)).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        pred_id = row["id"]
        signal = row["signal"]
        entry = row["entry_price"] or 0
        agent_votes_raw = row["agent_votes"]

        pct_change = (current_price - entry) / entry if entry else 0
        THRESHOLD = 0.005

        if signal == "BUY_CALL":
            correct = int(pct_change >= THRESHOLD)
        elif signal == "BUY_PUT":
            correct = int(pct_change <= -THRESHOLD)
        else:
            correct = int(abs(pct_change) < THRESHOLD)

        c.execute("""
            UPDATE predictions
            SET outcome = ?, outcome_price = ?, outcome_checked_at = ?, was_correct = ?
            WHERE id = ?
        """, ("CORRECT" if correct else "WRONG", current_price, now, correct, pred_id))

        # Update per-agent performance
        try:
            votes = json.loads(agent_votes_raw or "{}")
            for agent_name, agent_vote in votes.items():
                # Agent is "correct" if they voted with the system signal AND system was correct,
                # OR if they voted against and system was wrong
                agent_agrees_with_system = (agent_vote == signal)
                agent_correct = int(agent_agrees_with_system == bool(correct))

                c.execute("""
                    INSERT INTO agent_performance
                    (agent_name, symbol, vote, system_signal, confidence,
                     was_correct, created_at, prediction_id)
                    VALUES (?, ?, ?, ?, 70, ?, ?, ?)
                """, (agent_name, symbol, agent_vote, signal, agent_correct,
                      now, pred_id))
        except Exception as e:
            logger.warning(f"Agent performance update: {e}")

    conn.commit()
    _recalculate_weights(c)
    conn.commit()
    conn.close()


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

    def save_prediction(self, symbol: str, signal: str, confidence: float,
                        entry_price: float, target_price: float, stop_loss: float,
                        agent_votes: dict) -> int:
        return save_prediction(symbol, signal, confidence, entry_price,
                               target_price, stop_loss, agent_votes)

    def verify_outcomes(self, symbol: str, current_price: float):
        try:
            verify_outcomes(symbol, current_price)
        except Exception as e:
            logger.warning(f"verify_outcomes: {e}")

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
