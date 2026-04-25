"""
Agent Learning System
Tracks prediction accuracy per agent, adjusts confidence weights over time.
Uses SQLite for persistence — survives server restarts.
"""
import sqlite3
import json
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "predictions.db")

INITIAL_WEIGHTS = {
    "Price Action Agent": 1.0,
    "Technical Agent": 1.0,
    "Volume Agent": 1.0,
    "Sentiment Agent": 0.8,
    "Options Flow Agent": 0.9,
    "Momentum Agent": 1.0,
    "Risk Agent": 1.0,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            confidence REAL,
            entry_price REAL,
            stop_loss REAL,
            target_price REAL,
            vote_tally TEXT,
            agent_votes TEXT,
            created_at TEXT NOT NULL,
            outcome TEXT,
            outcome_price REAL,
            outcome_checked_at TEXT,
            was_correct INTEGER
        );

        CREATE TABLE IF NOT EXISTS agent_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            symbol TEXT,
            vote TEXT,
            confidence REAL,
            was_correct INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_weights (
            agent_name TEXT PRIMARY KEY,
            weight REAL NOT NULL DEFAULT 1.0,
            total_predictions INTEGER DEFAULT 0,
            correct_predictions INTEGER DEFAULT 0,
            accuracy REAL DEFAULT 0.5,
            updated_at TEXT
        );
    """)

    # Seed initial weights if empty
    for agent, weight in INITIAL_WEIGHTS.items():
        c.execute("""
            INSERT OR IGNORE INTO agent_weights
            (agent_name, weight, total_predictions, correct_predictions, accuracy, updated_at)
            VALUES (?, ?, 0, 0, 0.5, ?)
        """, (agent, weight, datetime.now(timezone.utc).isoformat()))

    conn.commit()
    conn.close()


def save_prediction(symbol: str, judgment: dict, agent_votes: list) -> int:
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("""
        INSERT INTO predictions
        (symbol, signal, confidence, entry_price, stop_loss, target_price,
         vote_tally, agent_votes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        judgment.get("signal"),
        judgment.get("confidence"),
        judgment.get("entry_price"),
        judgment.get("stop_loss"),
        judgment.get("target_price"),
        json.dumps(judgment.get("vote_tally", {})),
        json.dumps(agent_votes),
        now,
    ))
    pred_id = c.lastrowid
    conn.commit()
    conn.close()
    return pred_id


def check_and_update_outcomes(symbol: str, current_price: float):
    """
    Check predictions from 1+ days ago and mark them correct/wrong.
    A BUY is correct if price went up >= 0.5%.
    A SELL is correct if price went down >= 0.5%.
    HOLD is correct if price moved < 0.5% in either direction.
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
        entry = row["entry_price"]
        agent_votes_raw = row["agent_votes"]

        pct_change = (current_price - entry) / entry if entry else 0
        THRESHOLD = 0.005  # 0.5%

        if signal == "BUY":
            correct = pct_change >= THRESHOLD
        elif signal == "SELL":
            correct = pct_change <= -THRESHOLD
        else:  # HOLD
            correct = abs(pct_change) < THRESHOLD

        c.execute("""
            UPDATE predictions
            SET outcome = ?, outcome_price = ?, outcome_checked_at = ?, was_correct = ?
            WHERE id = ?
        """, (
            "CORRECT" if correct else "WRONG",
            current_price, now, 1 if correct else 0, pred_id
        ))

        # Update per-agent performance
        try:
            votes = json.loads(agent_votes_raw or "[]")
            for vote in votes:
                agent_name = vote.get("agent", "")
                agent_vote = vote.get("vote", "HOLD")
                agent_conf = vote.get("confidence", 50)

                # Was this agent's vote correct?
                if signal == "BUY":
                    agent_correct = agent_vote == "BUY" and correct or agent_vote == "SELL" and not correct
                    agent_correct = 1 if agent_vote == signal and correct else (0 if agent_vote == signal and not correct else None)
                elif signal == "SELL":
                    agent_correct = 1 if agent_vote == signal and correct else (0 if agent_vote == signal and not correct else None)
                else:
                    agent_correct = None

                if agent_correct is not None:
                    c.execute("""
                        INSERT INTO agent_performance
                        (agent_name, symbol, vote, confidence, was_correct, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (agent_name, symbol, agent_vote, agent_conf, agent_correct, now))
        except Exception as e:
            logger.warning(f"Error updating agent performance: {e}")

    conn.commit()

    # Recalculate weights for agents with enough data
    _recalculate_weights(c)
    conn.commit()
    conn.close()


def _recalculate_weights(c: sqlite3.Cursor):
    """Bayesian-style weight adjustment based on observed accuracy."""
    agents = c.execute("SELECT DISTINCT agent_name FROM agent_performance").fetchall()
    for row in agents:
        agent = row["agent_name"]
        stats = c.execute("""
            SELECT COUNT(*) as total,
                   SUM(was_correct) as correct
            FROM agent_performance
            WHERE agent_name = ?
        """, (agent,)).fetchone()

        total = stats["total"] or 0
        correct = stats["correct"] or 0

        if total >= 5:
            # Bayesian accuracy with Laplace smoothing
            accuracy = (correct + 1) / (total + 2)
            # Weight: 0.5 to 1.5 range based on accuracy
            weight = 0.5 + accuracy
        else:
            accuracy = 0.5
            weight = INITIAL_WEIGHTS.get(agent, 1.0)

        c.execute("""
            UPDATE agent_weights
            SET weight = ?, total_predictions = ?, correct_predictions = ?,
                accuracy = ?, updated_at = ?
            WHERE agent_name = ?
        """, (
            round(weight, 4), total, correct,
            round(accuracy, 4),
            datetime.now(timezone.utc).isoformat(),
            agent,
        ))


def get_agent_weights() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT agent_name, weight, accuracy, total_predictions, correct_predictions FROM agent_weights").fetchall()
    conn.close()
    return {
        r["agent_name"]: {
            "weight": r["weight"],
            "accuracy": r["accuracy"],
            "total": r["total_predictions"],
            "correct": r["correct_predictions"],
        }
        for r in rows
    }


def get_recent_predictions(symbol: str, limit: int = 10) -> list:
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


def get_accuracy_stats() -> dict:
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) as n FROM predictions WHERE outcome IS NOT NULL").fetchone()["n"]
    correct = conn.execute("SELECT COUNT(*) as n FROM predictions WHERE was_correct = 1").fetchone()["n"]
    by_signal = conn.execute("""
        SELECT signal,
               COUNT(*) as total,
               SUM(was_correct) as correct
        FROM predictions
        WHERE outcome IS NOT NULL
        GROUP BY signal
    """).fetchall()
    conn.close()

    accuracy = correct / total if total > 0 else 0
    breakdown = {r["signal"]: {"total": r["total"], "correct": r["correct"] or 0} for r in by_signal}
    return {"total": total, "correct": correct, "accuracy": round(accuracy, 4), "by_signal": breakdown}


# ─────────────────────────────────────────────────────────────────────────────
# LearningSystem class — wraps module functions for clean server.py interface
# ─────────────────────────────────────────────────────────────────────────────
class LearningSystem:
    """Unified interface for prediction tracking and agent weight management."""

    def __init__(self):
        init_db()

    def get_weights(self) -> dict:
        """Return {agent_name: weight_float}."""
        weights_data = get_agent_weights()
        return {name: info["weight"] for name, info in weights_data.items()}

    def save_prediction(
        self, symbol: str, signal: str, confidence: float,
        entry_price: float, target_price: float, stop_loss: float,
        agent_votes: dict,
    ) -> int:
        """Save a prediction using flat parameters instead of nested judgment dict."""
        judgment = {
            "signal": signal,
            "confidence": confidence,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "vote_tally": {},
        }
        # Convert agent_votes dict {agent_name: vote} to list for legacy format
        votes_list = [{"agent": k, "vote": v, "confidence": 70} for k, v in (agent_votes or {}).items()]
        return save_prediction(symbol, judgment, votes_list)

    def verify_outcomes(self, symbol: str, current_price: float):
        """Check and update outcomes for old predictions."""
        try:
            check_and_update_outcomes(symbol, current_price)
        except Exception as e:
            logger.warning(f"verify_outcomes error: {e}")

    def get_accuracy_report(self) -> dict:
        """Full accuracy report including per-agent weights."""
        stats = get_accuracy_stats()
        weights = get_agent_weights()
        return {"overall": stats, "agents": weights}

    def get_history(self, symbol: str) -> dict:
        """Recent prediction history for a symbol."""
        preds = get_recent_predictions(symbol, limit=20)
        return {"symbol": symbol, "history": preds}
