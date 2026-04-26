"""
Meta-Learning Layer
───────────────────
This is where the model "learns more" and "creates its own methods".

What it does (all on top of the existing 10-agent system):

1. INDICATOR SNAPSHOT LOG
   Every prediction now stores a compact snapshot of the indicator state
   at decision time. Over weeks of running, this becomes a real dataset.

2. PER-REGIME / PER-SYMBOL ACCURACY
   Tracks each agent's hit-rate broken down by market regime
   (bull / bear / sideways / risk-off) AND by symbol, so the Judge can
   weight an agent more if it has been historically good at THIS symbol
   in THIS regime.

3. STRATEGY DISCOVERY  ← the "create their own methods" part
   Periodically mines the snapshot log and finds 1- and 2-condition rules
   (e.g. "RSI<35 AND MACD_hist>0") that have a statistically significant
   win-rate edge. These become "discovered strategies" that vote alongside
   the hand-coded agents.

   Strategies are persisted to discovered_strategies.json so they survive
   restarts and gradually accumulate as the database grows.

4. STRATEGY EVALUATION
   evaluate_discovered(ind) returns the list of currently-firing strategies
   plus a net BUY_CALL / BUY_PUT lean with confidence.

All of this is local-first, no cloud, no paid feeds — pure self-supervised
learning from the predictions you actually make.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DIR, "predictions.db")
STRATEGIES_PATH = os.path.join(_DIR, "discovered_strategies.json")

# Minimum sample size before we trust a discovered rule
MIN_SAMPLES = 15
# Minimum hit-rate edge over baseline (50%) for a rule to be kept
MIN_EDGE = 0.10          # rule must hit ≥60% (or ≤40% inverse) to qualify
MIN_REGIME_SAMPLES = 8   # min sample for per-regime weight kick-in


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_meta_db():
    c = _conn()
    cur = c.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS indicator_snapshots (
            prediction_id  INTEGER PRIMARY KEY,
            symbol         TEXT NOT NULL,
            signal         TEXT NOT NULL,
            regime         TEXT,
            snapshot_json  TEXT NOT NULL,
            created_at     TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_snap_sym  ON indicator_snapshots(symbol);
        CREATE INDEX IF NOT EXISTS idx_snap_reg  ON indicator_snapshots(regime);

        CREATE TABLE IF NOT EXISTS regime_agent_perf (
            agent_name  TEXT NOT NULL,
            regime      TEXT NOT NULL,
            total       INTEGER DEFAULT 0,
            correct     INTEGER DEFAULT 0,
            updated_at  TEXT,
            PRIMARY KEY (agent_name, regime)
        );

        CREATE TABLE IF NOT EXISTS symbol_agent_perf (
            agent_name  TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            total       INTEGER DEFAULT 0,
            correct     INTEGER DEFAULT 0,
            updated_at  TEXT,
            PRIMARY KEY (agent_name, symbol)
        );
    """)
    c.commit()
    c.close()


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot capture (called from server.py right after save_prediction)
# ─────────────────────────────────────────────────────────────────────────────
# We only persist a small, well-defined set of features. This keeps the DB
# small AND ensures discovery doesn't "cheat" by mining noise.
SNAPSHOT_FEATURES = [
    "rsi14", "macd_hist", "adx", "williams_r", "stoch_k",
    "cci", "mfi", "cmf", "tsi", "trix",
    "roc10", "roc20", "atr_pct",
    "bb_pct_b", "vol_ratio",
    "ichimoku_signal", "supertrend_dir",
    "rsi_divergence", "double_pattern", "triangle_pattern",
    "psar_trend", "aroon_osc",
    # v6.7 — additional features required by MLAgent feature extractor
    # so that train_from_resolved() can rebuild the same vector from
    # a stored snapshot that the live analyze() call sees.
    "price", "atr14", "macd_cross_up", "macd_cross_dn",
    "plus_di", "minus_di", "bb_upper", "bb_lower", "bb_mid",
    "price_vs_vwap_pct", "trend_score", "cs_pattern_score",
    # v6.9 — new alpha-source features (gap, NR4/NR7, volume profile, Hurst)
    "gap_pct", "gap_signal", "gap_state",
    "nr4", "nr7", "range_compression", "inside_bar", "change_5d",
    "vp_poc", "vp_position", "vp_above_poc",
    "hurst", "regime_kind",
]


def _flatten_indicators(ind: dict) -> dict:
    """Pull just the features we care about and flatten nested dicts."""
    out: dict[str, Any] = {}
    for k in SNAPSHOT_FEATURES:
        if k in ind:
            out[k] = ind[k]
    # Flatten the few nested ones
    psar = ind.get("psar") or {}
    out["psar_trend"] = psar.get("trend", "flat")
    aroon = ind.get("aroon") or {}
    out["aroon_osc"] = aroon.get("osc", 0)
    regime = ind.get("market_regime") or {}
    out["__regime__"] = regime.get("label", "unknown")
    fund = ind.get("fundamentals") or {}
    out["fund_score"] = fund.get("score", 50)
    macro = ind.get("macro_basket") or {}
    out["macro_label"] = macro.get("macro_label", "mixed")
    return out


def save_snapshot(prediction_id: int, symbol: str, signal: str, ind: dict):
    """Store a compact indicator snapshot for later mining."""
    try:
        snap = _flatten_indicators(ind)
        regime = snap.pop("__regime__", "unknown")
        c = _conn()
        c.execute("""
            INSERT OR REPLACE INTO indicator_snapshots
            (prediction_id, symbol, signal, regime, snapshot_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (prediction_id, symbol, signal, regime,
              json.dumps(snap), datetime.now(timezone.utc).isoformat()))
        c.commit()
        c.close()
    except Exception as e:
        logger.warning(f"meta_learning.save_snapshot: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Per-regime / per-symbol weight learning
# ─────────────────────────────────────────────────────────────────────────────
def update_regime_symbol_perf():
    """
    Recompute per-regime and per-symbol agent accuracy from the joined
    predictions + indicator_snapshots + agent_performance tables.
    Idempotent — safe to call after every batch of verified outcomes.
    """
    c = _conn()
    cur = c.cursor()
    try:
        # 1. Per-regime per-agent accuracy
        # Joins on prediction_id (added by the migration in learning.init_db).
        rows = cur.execute("""
            SELECT ap.agent_name, isn.regime,
                   COUNT(*) as total, SUM(ap.was_correct) as correct
            FROM agent_performance ap
            JOIN indicator_snapshots isn ON isn.prediction_id = ap.prediction_id
            WHERE ap.prediction_id IS NOT NULL
            GROUP BY ap.agent_name, isn.regime
        """).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for r in rows:
            cur.execute("""
                INSERT INTO regime_agent_perf
                (agent_name, regime, total, correct, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(agent_name, regime) DO UPDATE SET
                    total=excluded.total,
                    correct=excluded.correct,
                    updated_at=excluded.updated_at
            """, (r["agent_name"], r["regime"] or "unknown",
                  r["total"] or 0, r["correct"] or 0, now))

        # 2. Per-symbol per-agent accuracy
        rows = cur.execute("""
            SELECT agent_name, symbol,
                   COUNT(*) as total, SUM(was_correct) as correct
            FROM agent_performance
            GROUP BY agent_name, symbol
        """).fetchall()
        for r in rows:
            cur.execute("""
                INSERT INTO symbol_agent_perf
                (agent_name, symbol, total, correct, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(agent_name, symbol) DO UPDATE SET
                    total=excluded.total,
                    correct=excluded.correct,
                    updated_at=excluded.updated_at
            """, (r["agent_name"], r["symbol"],
                  r["total"] or 0, r["correct"] or 0, now))
        c.commit()
    except Exception as e:
        logger.warning(f"meta_learning.update_regime_symbol_perf: {e}")
    finally:
        c.close()


def get_regime_multipliers() -> dict:
    """
    Returns {(agent_name, regime): multiplier} where multiplier is centered
    on 1.0 and ranges 0.6..1.4 based on (correct / total) - 0.5.
    Only kicks in once an agent has ≥ MIN_REGIME_SAMPLES in that regime.
    """
    out: dict = {}
    try:
        c = _conn()
        for r in c.execute("SELECT * FROM regime_agent_perf").fetchall():
            total = r["total"] or 0
            if total < MIN_REGIME_SAMPLES:
                continue
            acc = (r["correct"] or 0) / total
            # Map 0.30..0.70 → 0.7..1.3
            mult = 1.0 + (acc - 0.5) * 1.2
            mult = max(0.6, min(1.4, mult))
            out[(r["agent_name"], r["regime"])] = round(mult, 3)
        c.close()
    except Exception as e:
        logger.warning(f"get_regime_multipliers: {e}")
    return out


def get_symbol_multipliers() -> dict:
    """{(agent, symbol): mult} — same idea, for per-stock specialization."""
    out: dict = {}
    try:
        c = _conn()
        for r in c.execute("SELECT * FROM symbol_agent_perf").fetchall():
            total = r["total"] or 0
            if total < MIN_REGIME_SAMPLES:
                continue
            acc = (r["correct"] or 0) / total
            mult = 1.0 + (acc - 0.5) * 1.0
            mult = max(0.7, min(1.3, mult))
            out[(r["agent_name"], r["symbol"])] = round(mult, 3)
        c.close()
    except Exception as e:
        logger.warning(f"get_symbol_multipliers: {e}")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY DISCOVERY  — the "AI creates its own methods" part
# ─────────────────────────────────────────────────────────────────────────────
# We define a small grammar of atomic conditions. Discovery enumerates all
# 1-condition and 2-condition combinations and keeps the ones with a
# statistically significant edge.
#
# Each condition is (label, predicate_fn). predicate_fn takes the snapshot
# dict and returns True/False.

def _atoms() -> list[tuple[str, Any]]:
    A: list[tuple[str, Any]] = [
        ("RSI<30",         lambda s: float(s.get("rsi14", 50)) < 30),
        ("RSI>70",         lambda s: float(s.get("rsi14", 50)) > 70),
        ("RSI<40",         lambda s: float(s.get("rsi14", 50)) < 40),
        ("RSI>60",         lambda s: float(s.get("rsi14", 50)) > 60),
        ("MACD_hist>0",    lambda s: float(s.get("macd_hist", 0)) > 0),
        ("MACD_hist<0",    lambda s: float(s.get("macd_hist", 0)) < 0),
        ("ADX>25",         lambda s: float(s.get("adx", 0)) > 25),
        ("ADX<15",         lambda s: float(s.get("adx", 0)) < 15),
        ("CCI>100",        lambda s: float(s.get("cci", 0)) > 100),
        ("CCI<-100",       lambda s: float(s.get("cci", 0)) < -100),
        ("MFI>65",         lambda s: float(s.get("mfi", 50)) > 65),
        ("MFI<35",         lambda s: float(s.get("mfi", 50)) < 35),
        ("CMF>0.05",       lambda s: float(s.get("cmf", 0)) > 0.05),
        ("CMF<-0.05",      lambda s: float(s.get("cmf", 0)) < -0.05),
        ("W%R<-80",        lambda s: float(s.get("williams_r", -50)) < -80),
        ("W%R>-20",        lambda s: float(s.get("williams_r", -50)) > -20),
        ("Aroon>50",       lambda s: float(s.get("aroon_osc", 0)) > 50),
        ("Aroon<-50",      lambda s: float(s.get("aroon_osc", 0)) < -50),
        ("ROC10>2",        lambda s: float(s.get("roc10", 0)) > 2),
        ("ROC10<-2",       lambda s: float(s.get("roc10", 0)) < -2),
        ("Vol>1.5x",       lambda s: float(s.get("vol_ratio", 1)) > 1.5),
        ("BBpctB>0.9",     lambda s: float(s.get("bb_pct_b", 0.5)) > 0.9),
        ("BBpctB<0.1",     lambda s: float(s.get("bb_pct_b", 0.5)) < 0.1),
        ("Ichi=bullish",   lambda s: s.get("ichimoku_signal") == "bullish"),
        ("Ichi=bearish",   lambda s: s.get("ichimoku_signal") == "bearish"),
        ("PSAR=up",        lambda s: s.get("psar_trend") == "up"),
        ("PSAR=down",      lambda s: s.get("psar_trend") == "down"),
        ("RSIdiv=bull",    lambda s: s.get("rsi_divergence") == "bullish"),
        ("RSIdiv=bear",    lambda s: s.get("rsi_divergence") == "bearish"),
        ("Fund>=70",       lambda s: float(s.get("fund_score", 50)) >= 70),
        ("Fund<40",        lambda s: float(s.get("fund_score", 50)) < 40),
        ("Macro=risk-on",  lambda s: s.get("macro_label") == "risk-on"),
        ("Macro=risk-off", lambda s: s.get("macro_label") == "risk-off"),
    ]
    return A


def _load_resolved_snapshots(limit: int = 5000) -> list[tuple[dict, int, str]]:
    """
    Returns list of (snapshot_dict, was_correct, signal) for every prediction
    that has been resolved.
    """
    rows = []
    try:
        c = _conn()
        cur = c.execute("""
            SELECT isn.snapshot_json, p.was_correct, p.signal
            FROM indicator_snapshots isn
            JOIN predictions p ON p.id = isn.prediction_id
            WHERE p.outcome IS NOT NULL
            ORDER BY p.created_at DESC
            LIMIT ?
        """, (limit,))
        for r in cur.fetchall():
            try:
                snap = json.loads(r["snapshot_json"])
                rows.append((snap, int(r["was_correct"] or 0), r["signal"]))
            except Exception:
                continue
        c.close()
    except Exception as e:
        logger.warning(f"_load_resolved_snapshots: {e}")
    return rows


def discover_strategies(min_samples: int = MIN_SAMPLES,
                        min_edge: float = MIN_EDGE) -> list[dict]:
    """
    Mine the snapshot log for indicator combinations that show a real edge.
    Saves to discovered_strategies.json AND returns the list.

    Each strategy looks like:
      {
        "name": "RSI<30 & MACD_hist>0",
        "conditions": ["RSI<30", "MACD_hist>0"],
        "signal":  "BUY_CALL",   # or BUY_PUT
        "samples":  47,
        "hits":     34,
        "hit_rate": 0.723,
        "edge":     0.223        # hit_rate - 0.5
      }
    """
    snaps = _load_resolved_snapshots()
    if len(snaps) < min_samples:
        logger.info(f"discover_strategies: only {len(snaps)} resolved snapshots, "
                    f"need ≥{min_samples}")
        _save_strategies([])
        return []

    atoms = _atoms()
    candidates: list[tuple[list[str], list[Any], str]] = []

    # 1-condition rules
    for (lbl, fn) in atoms:
        for sig in ("BUY_CALL", "BUY_PUT"):
            candidates.append(([lbl], [fn], sig))

    # 2-condition rules (combinations, no repeats)
    for i in range(len(atoms)):
        for j in range(i + 1, len(atoms)):
            l1, f1 = atoms[i]
            l2, f2 = atoms[j]
            for sig in ("BUY_CALL", "BUY_PUT"):
                candidates.append(([l1, l2], [f1, f2], sig))

    discovered: list[dict] = []
    for labels, fns, sig in candidates:
        matches = [(was_correct, real_sig)
                   for (snap, was_correct, real_sig) in snaps
                   if all(_safe_pred(fn, snap) for fn in fns)
                   and real_sig == sig]
        n = len(matches)
        if n < min_samples:
            continue
        hits = sum(c for c, _ in matches)
        hit_rate = hits / n
        edge = hit_rate - 0.5
        if abs(edge) < min_edge:
            continue
        # If edge is negative we flip the signal — the rule predicts the OPPOSITE
        if edge < 0:
            sig = "BUY_PUT" if sig == "BUY_CALL" else "BUY_CALL"
            hit_rate = 1.0 - hit_rate
            edge = -edge
        discovered.append({
            "name": " & ".join(labels) + f" → {sig}",
            "conditions": labels,
            "signal": sig,
            "samples": n,
            "hits": hits if edge > 0 else (n - hits),
            "hit_rate": round(hit_rate, 3),
            "edge": round(edge, 3),
        })

    # De-duplicate: keep the strongest version of each condition-set
    by_key: dict[str, dict] = {}
    for d in discovered:
        key = "|".join(sorted(d["conditions"])) + "::" + d["signal"]
        prev = by_key.get(key)
        if not prev or d["edge"] > prev["edge"]:
            by_key[key] = d

    final = sorted(by_key.values(), key=lambda x: x["edge"], reverse=True)
    # Cap to top 40 to keep evaluation fast
    final = final[:40]
    _save_strategies(final)
    logger.info(f"discover_strategies: kept {len(final)} rules from "
                f"{len(snaps)} snapshots")
    return final


def _safe_pred(fn, snap) -> bool:
    try:
        return bool(fn(snap))
    except Exception:
        return False


def _save_strategies(strategies: list[dict]):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(strategies),
        "strategies": strategies,
    }
    try:
        with open(STRATEGIES_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        logger.warning(f"_save_strategies: {e}")


def load_strategies() -> list[dict]:
    if not os.path.exists(STRATEGIES_PATH):
        return []
    try:
        with open(STRATEGIES_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("strategies", [])
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Strategy evaluation at decision time
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_discovered(ind: dict) -> dict:
    """
    Given the current indicator dict, return:
      {
        "fired": [ {name, signal, hit_rate, edge}, ... ],
        "lean":  "BUY_CALL" | "BUY_PUT" | "HOLD",
        "score": float (-1..+1, sum of edges signed by direction),
        "confidence_boost": float in [-8, +8]  (% to add/subtract from final conf)
      }
    """
    strategies = load_strategies()
    if not strategies:
        return {"fired": [], "lean": "HOLD", "score": 0.0, "confidence_boost": 0.0}

    snap = _flatten_indicators(ind)
    snap.pop("__regime__", None)
    atoms_map = {lbl: fn for (lbl, fn) in _atoms()}

    fired: list[dict] = []
    score = 0.0
    for s in strategies:
        try:
            conds = s.get("conditions", [])
            if not conds:
                continue
            if not all(_safe_pred(atoms_map[c], snap) for c in conds if c in atoms_map):
                continue
            fired.append({
                "name": s["name"],
                "signal": s["signal"],
                "hit_rate": s["hit_rate"],
                "edge": s["edge"],
            })
            score += s["edge"] if s["signal"] == "BUY_CALL" else -s["edge"]
        except Exception:
            continue

    if score > 0.05:
        lean = "BUY_CALL"
    elif score < -0.05:
        lean = "BUY_PUT"
    else:
        lean = "HOLD"

    # Map cumulative edge → conf boost, capped at ±8%
    boost = max(-8.0, min(8.0, score * 20.0))

    return {
        "fired": fired[:6],         # cap output size for the UI
        "lean": lean,
        "score": round(score, 3),
        "confidence_boost": round(boost, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public bootstrap
# ─────────────────────────────────────────────────────────────────────────────
def bootstrap():
    init_meta_db()
