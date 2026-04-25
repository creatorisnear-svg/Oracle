"""
Meta-Judge: probability calibration + stacked-ensemble upgrade.

The hand-crafted JudgeAgent in agents.py produces a heuristic confidence
score. That score is *informative* (more conviction → higher number) but
it is not a true probability — a 70% judge confidence does not mean
"historically 70% of these win". This module fixes that in two ways:

  1. ISOTONIC CALIBRATION
     Reads resolved historical predictions, bins by (signal, raw_conf),
     fits a monotone-non-decreasing mapping raw_conf → win_rate using the
     Pool Adjacent Violators algorithm. Applying this to new predictions
     converts the judge's heuristic into an empirically-grounded probability.

  2. LOGISTIC STACKER
     Trains a tiny logistic regression on (per-agent signed vote) → outcome.
     Lets the system learn "which COMBINATION of agent votes predicts wins"
     instead of relying solely on the judge's hand-coded weighting + vetoes.
     Falls back to pass-through until enough samples accumulate.

Both layers are additive and CONSERVATIVE: they default to a pass-through
when sample sizes are too small, so they never make predictions worse than
the raw judge output.

Public API:
    apply_meta_judge(judgment, votes) -> dict
        Returns the same judgment dict with `confidence` replaced by the
        calibrated/stacked probability, plus `meta` field exposing the
        breakdown for transparency in the UI.

    get_calibration_report() -> dict
        Returns Brier score, sample counts, and bucket statistics so the
        frontend can show "how honest is the confidence number?".
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "predictions.db")

# ── Tuning ────────────────────────────────────────────────────────────────
# Minimum resolved samples per (signal) bucket before the calibrator activates.
# Below this, we pass through the raw judge confidence unchanged.
MIN_CALIB_SAMPLES = 30
# Minimum total resolved samples before the stacker trains. Stacker has
# more parameters (one per agent) so it needs more data than calibration.
MIN_STACKER_SAMPLES = 50
# Stacker blend weight cap. At full strength the stacker accounts for at
# most this fraction of the final probability — the judge always retains
# a meaningful voice.
MAX_STACKER_BLEND = 0.40
# How long to cache the fitted models before refitting from the DB.
REFIT_INTERVAL_SECS = 300
# Mapping signal → outcome label considered "win" in the predictions table.
# The verifier writes "win" / "loss" / "stop" / "expired" to outcome.
WIN_OUTCOMES = {"win", "target_hit"}


# ──────────────────────────────────────────────────────────────────────────
# Pool Adjacent Violators — pure-numpy isotonic regression.
# ──────────────────────────────────────────────────────────────────────────
def _isotonic_pav(x: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> np.ndarray:
    """Monotone non-decreasing fit of y vs x using PAV.

    Returns the fitted y values (same length as input). x must already be
    sorted ascending. This is the standard scikit-learn algorithm in ~30
    lines of numpy — no sklearn dependency.
    """
    n = len(y)
    if n == 0:
        return np.array([], dtype=float)
    if w is None:
        w = np.ones(n, dtype=float)
    # Active blocks: each block has (sum_w, sum_wy, start_idx, end_idx).
    # We merge adjacent blocks whenever the right block's mean is lower
    # than the left's — guaranteeing the resulting fit is non-decreasing.
    sums_w = w.astype(float).copy()
    sums_wy = (w * y).astype(float).copy()
    sizes = np.ones(n, dtype=int)
    means = sums_wy / np.maximum(sums_w, 1e-12)
    # Stack-based merge
    idx = 0
    stack: list[int] = []  # indices of block roots in the arrays above
    for i in range(n):
        stack.append(i)
        # Merge while top of stack violates monotonicity
        while len(stack) >= 2 and means[stack[-2]] > means[stack[-1]] - 1e-12:
            top = stack.pop()
            prev = stack[-1]
            sums_w[prev] += sums_w[top]
            sums_wy[prev] += sums_wy[top]
            sizes[prev] += sizes[top]
            means[prev] = sums_wy[prev] / max(sums_w[prev], 1e-12)
    # Expand each block back out to its original positions
    out = np.empty(n, dtype=float)
    cursor = 0
    for root in stack:
        sz = sizes[root]
        out[cursor:cursor + sz] = means[root]
        cursor += sz
    return out


# ──────────────────────────────────────────────────────────────────────────
# Logistic regression — tiny pure-numpy gradient-descent fit.
# ──────────────────────────────────────────────────────────────────────────
def _logistic_fit(X: np.ndarray, y: np.ndarray, l2: float = 1.0,
                  iters: int = 400, lr: float = 0.05) -> tuple[np.ndarray, float]:
    """Fit logistic regression with L2 regularisation.

    Returns (coefficients, intercept). Uses simple full-batch gradient
    descent — fine for ≤thousands of samples and ≤dozens of features
    which is exactly our scale (predictions table maxes out around the
    low thousands of rows for a personal trading dashboard).
    """
    n, d = X.shape
    if n == 0:
        return np.zeros(d), 0.0
    w = np.zeros(d, dtype=float)
    b = 0.0
    for _ in range(iters):
        z = X @ w + b
        # Numerically-stable sigmoid
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        err = p - y
        grad_w = (X.T @ err) / n + l2 * w / n
        grad_b = err.mean()
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b


def _logistic_predict(X: np.ndarray, w: np.ndarray, b: float) -> np.ndarray:
    z = X @ w + b
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


# ──────────────────────────────────────────────────────────────────────────
# Cached fitted models. Refits from DB at most every REFIT_INTERVAL_SECS.
# ──────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_state: dict = {
    "fitted_at": 0.0,
    "calibrator": None,    # dict: signal → (sorted_xs, fitted_ys) for interpolation
    "stacker": None,       # dict: agent_index, weights, intercept
    "report": None,        # last computed report
}


def _load_resolved_samples() -> list[dict]:
    """Pull resolved predictions with their agent vote dictionaries."""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, signal, confidence, agent_votes, was_correct, outcome
            FROM predictions
            WHERE was_correct IS NOT NULL
              AND signal IN ('BUY_CALL', 'BUY_PUT')
              AND confidence IS NOT NULL
            ORDER BY id ASC
        """).fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"meta_judge: failed to load samples: {e}")
        return []
    out = []
    for r in rows:
        try:
            votes = json.loads(r["agent_votes"] or "{}")
        except Exception:
            votes = {}
        out.append({
            "id": r["id"],
            "signal": r["signal"],
            "confidence": float(r["confidence"]),
            "votes": votes,
            "was_correct": int(r["was_correct"]),
        })
    return out


def _fit_calibrator(samples: list[dict]) -> dict | None:
    """Build per-signal isotonic calibrator: raw_conf → P(win)."""
    cal: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for sig in ("BUY_CALL", "BUY_PUT"):
        sub = [s for s in samples if s["signal"] == sig]
        if len(sub) < MIN_CALIB_SAMPLES:
            continue
        # Sort by confidence ascending so PAV gets monotone input
        sub.sort(key=lambda s: s["confidence"])
        xs = np.array([s["confidence"] for s in sub], dtype=float)
        ys = np.array([s["was_correct"] for s in sub], dtype=float)
        # Bin into 8-12 buckets to smooth before PAV — raw per-prediction
        # 0/1 outcomes are too noisy to fit directly.
        n_bins = min(12, max(4, len(sub) // 8))
        edges = np.quantile(xs, np.linspace(0, 1, n_bins + 1))
        # Drop duplicate edges (happens when many predictions share confidence)
        edges = np.unique(edges)
        if len(edges) < 3:
            continue
        bin_x: list[float] = []
        bin_y: list[float] = []
        bin_w: list[float] = []
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            if i == len(edges) - 2:
                mask = (xs >= lo) & (xs <= hi)
            else:
                mask = (xs >= lo) & (xs < hi)
            n_in = int(mask.sum())
            if n_in == 0:
                continue
            bin_x.append(float(xs[mask].mean()))
            bin_y.append(float(ys[mask].mean()))
            bin_w.append(float(n_in))
        if len(bin_x) < 2:
            continue
        bx = np.array(bin_x)
        by = np.array(bin_y)
        bw = np.array(bin_w)
        fitted = _isotonic_pav(bx, by, bw)
        # Squash extremes a touch so we never publish 0% or 100% confidence
        fitted = np.clip(fitted, 0.05, 0.95)
        cal[sig] = (bx, fitted)
    return cal or None


def _build_stacker_features(votes: dict, agent_index: dict[str, int]) -> np.ndarray:
    """One signed scalar per agent: +1 for BUY_CALL, -1 for BUY_PUT, 0 otherwise.

    This deliberately does NOT include the judge's confidence as a feature
    — the stacker should learn an INDEPENDENT view from the raw votes,
    so the blend in apply_meta_judge() combines two different signals.
    """
    x = np.zeros(len(agent_index), dtype=float)
    for name, vote in votes.items():
        idx = agent_index.get(name)
        if idx is None:
            continue
        if vote == "BUY_CALL":
            x[idx] = 1.0
        elif vote == "BUY_PUT":
            x[idx] = -1.0
    return x


def _fit_stacker(samples: list[dict]) -> dict | None:
    """Train per-direction stackers.

    We fit TWO models: one predicts P(win | signal=BUY_CALL),
    one predicts P(win | signal=BUY_PUT). This lets the stacker
    learn direction-specific patterns (e.g. "Sentiment Agent's
    BUY_CALL signals are reliable; its BUY_PUT signals are noise").
    """
    # Build agent index from the union of agents seen in history
    agents: set[str] = set()
    for s in samples:
        agents.update(s["votes"].keys())
    if not agents:
        return None
    agent_index = {name: i for i, name in enumerate(sorted(agents))}

    models: dict[str, tuple[np.ndarray, float]] = {}
    for sig in ("BUY_CALL", "BUY_PUT"):
        sub = [s for s in samples if s["signal"] == sig]
        if len(sub) < MIN_STACKER_SAMPLES:
            continue
        X = np.array([_build_stacker_features(s["votes"], agent_index) for s in sub])
        y = np.array([s["was_correct"] for s in sub], dtype=float)
        # If outcomes are all the same (very early days), skip
        if y.std() < 1e-3:
            continue
        w, b = _logistic_fit(X, y, l2=1.0, iters=400, lr=0.05)
        models[sig] = (w, b)
    if not models:
        return None
    return {"agent_index": agent_index, "models": models}


def _maybe_refit():
    now = time.time()
    if (_state["fitted_at"] and
            now - _state["fitted_at"] < REFIT_INTERVAL_SECS):
        return
    samples = _load_resolved_samples()
    cal = _fit_calibrator(samples)
    stk = _fit_stacker(samples)

    # ── Brier score for transparency ("are confidence numbers honest?")
    report = {
        "total_resolved": len(samples),
        "calibrator_active": cal is not None,
        "stacker_active": stk is not None,
        "by_signal": {},
    }
    for sig in ("BUY_CALL", "BUY_PUT"):
        sub = [s for s in samples if s["signal"] == sig]
        if not sub:
            continue
        confs = np.array([s["confidence"] / 100.0 for s in sub])
        outs = np.array([s["was_correct"] for s in sub], dtype=float)
        raw_brier = float(np.mean((confs - outs) ** 2))
        entry = {
            "samples": len(sub),
            "win_rate": float(outs.mean()),
            "raw_brier": round(raw_brier, 4),
        }
        if cal and sig in cal:
            cal_xs, cal_ys = cal[sig]
            calibrated = np.interp(np.array([s["confidence"] for s in sub]),
                                   cal_xs, cal_ys)
            entry["calibrated_brier"] = round(float(np.mean((calibrated - outs) ** 2)), 4)
        report["by_signal"][sig] = entry

    _state["calibrator"] = cal
    _state["stacker"] = stk
    _state["report"] = report
    _state["fitted_at"] = now


def _calibrate(signal: str, raw_conf: float) -> float | None:
    cal = _state["calibrator"]
    if not cal or signal not in cal:
        return None
    xs, ys = cal[signal]
    # Linear interpolation with end-clamping (np.interp does this by default)
    return float(np.interp(raw_conf, xs, ys))


def _stacker_predict(signal: str, votes: dict) -> float | None:
    stk = _state["stacker"]
    if not stk or signal not in stk["models"]:
        return None
    agent_index = stk["agent_index"]
    w, b = stk["models"][signal]
    x = _build_stacker_features(votes, agent_index).reshape(1, -1)
    return float(_logistic_predict(x, w, b)[0])


def apply_meta_judge(judgment: dict, votes: list[dict]) -> dict:
    """Augment a JudgeAgent verdict with calibrated + stacked confidence.

    INPUTS
        judgment: the dict returned by JudgeAgent.decide()
        votes:    the per-agent vote list (each dict has 'agent', 'vote')
    RETURNS
        The same judgment dict (mutated in place + returned) with:
          * `confidence` replaced by the calibrated/blended probability
            (still on the 0-100 scale the UI expects)
          * `meta` field with raw, calibrated, stacker, blended values
            and whether each layer was active.
    """
    sig = judgment.get("signal", "HOLD")
    raw_conf = float(judgment.get("confidence", 50.0))

    # HOLD doesn't have a directional probability — leave it alone.
    if sig == "HOLD":
        judgment["meta"] = {"applied": False, "reason": "HOLD"}
        return judgment

    try:
        with _lock:
            _maybe_refit()
            calibrated = _calibrate(sig, raw_conf)
            votes_dict = {v["agent"]: v["vote"] for v in votes}
            stacker_p = _stacker_predict(sig, votes_dict)
    except Exception as e:
        logger.warning(f"meta_judge: failed to apply, passing through: {e}")
        judgment["meta"] = {"applied": False, "error": str(e)}
        return judgment

    # ── Blend ─────────────────────────────────────────────────────────────
    # Both layers are OPTIONAL — if either is unavailable we fall back
    # gracefully. The blend never lets the stacker dominate (capped at
    # MAX_STACKER_BLEND = 40%) so the carefully-tuned judge logic isn't
    # overruled by what could be a noisy meta-model in early days.
    base_prob = (calibrated if calibrated is not None else raw_conf / 100.0)
    if stacker_p is not None:
        # Blend strength scales with confidence in the stacker — agreement
        # with the calibrator means we trust the blend; disagreement means
        # we hedge (blend halfway). This stops the stacker from overriding
        # the judge during regime shifts where it's been retrained on
        # stale data.
        agreement = 1.0 - min(1.0, abs(stacker_p - base_prob) * 2.0)
        blend_w = MAX_STACKER_BLEND * (0.5 + 0.5 * agreement)
        final_prob = (1.0 - blend_w) * base_prob + blend_w * stacker_p
    else:
        blend_w = 0.0
        final_prob = base_prob

    # Convert back to the 0-100 scale the UI expects, clamped.
    final_conf = round(max(40.0, min(95.0, final_prob * 100.0)), 1)

    judgment["meta"] = {
        "applied": True,
        "raw_confidence": round(raw_conf, 1),
        "calibrated": round(calibrated * 100, 1) if calibrated is not None else None,
        "stacker": round(stacker_p * 100, 1) if stacker_p is not None else None,
        "blend_weight": round(blend_w, 3),
        "final": final_conf,
    }
    judgment["confidence"] = final_conf
    return judgment


def get_calibration_report() -> dict:
    """Snapshot of model state — used by /api/admin endpoints + UI badge."""
    try:
        with _lock:
            _maybe_refit()
            return _state["report"] or {"total_resolved": 0,
                                         "calibrator_active": False,
                                         "stacker_active": False,
                                         "by_signal": {}}
    except Exception as e:
        logger.warning(f"meta_judge.get_calibration_report: {e}")
        return {"error": str(e), "total_resolved": 0,
                "calibrator_active": False, "stacker_active": False}
