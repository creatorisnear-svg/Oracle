"""Regime-aware Kelly criterion position sizer.

The Kelly fraction tells you what % of your bankroll to risk on a single trade
to maximize long-run growth. Formula:

    f* = (p * b - q) / b

where p = win probability, q = 1 - p, b = win/loss ratio (R:R).

We derive p and b empirically per VOLATILITY REGIME from the back-test
(see tests/compute_regime_stats.py → regime_stats.json), then scale by the
signal's confidence. We use HALF-KELLY (a standard safety practice — full
Kelly is too aggressive in real markets due to estimation error) and cap
the result to a max position size.
"""
import json, os
from typing import Optional

_STATS_PATH = os.path.join(os.path.dirname(__file__), "regime_stats.json")
_DEFAULT_STATS = {
    "low_vol":  {"hit_rate": 0.55, "avg_RR": 0.9},
    "normal":   {"hit_rate": 0.52, "avg_RR": 1.2},
    "high_vol": {"hit_rate": 0.45, "avg_RR": 1.6},
}
_HALF_KELLY = 0.5    # safety multiplier — never use full Kelly in real markets
_MAX_POSITION_PCT = 10.0  # never recommend more than 10% of bankroll per trade
_MIN_POSITION_PCT = 0.0   # may be 0 (skip the trade)

_cached_stats: Optional[dict] = None


def _load_stats() -> dict:
    global _cached_stats
    if _cached_stats is not None:
        return _cached_stats
    try:
        with open(_STATS_PATH) as f:
            data = json.load(f)
        # Ensure every regime has the required keys
        for reg in _DEFAULT_STATS:
            if reg not in data or data[reg].get("n", 0) < 5:
                data[reg] = _DEFAULT_STATS[reg]
        _cached_stats = data
    except Exception:
        _cached_stats = _DEFAULT_STATS.copy()
    return _cached_stats


def reload_stats() -> dict:
    """Force the next _load_stats() call to re-read regime_stats.json from disk.
    Called by the auto-refresh background task after regenerating the file."""
    global _cached_stats
    _cached_stats = None
    return _load_stats()


def regime_for_atr_pct(atr_pct: float) -> str:
    if atr_pct < 1.5: return "low_vol"
    if atr_pct > 4.0: return "high_vol"
    return "normal"


def kelly_fraction(p: float, b: float) -> float:
    """Pure Kelly formula. p = win prob, b = win/loss ratio. Clamped to [0, 1]."""
    if b <= 0 or p <= 0:
        return 0.0
    f = (p * b - (1 - p)) / b
    return max(0.0, min(1.0, f))


def compute_position_size(
    *, signal: str, confidence: float, entry: float, target: float, stop: float,
    atr_pct: float,
) -> dict:
    """Return a dict with the recommended Kelly position size + diagnostics.

    confidence — 0..100 from the JudgeAgent
    """
    if signal == "HOLD" or entry <= 0:
        return {
            "kelly_pct": 0.0, "dollars_per_10k": 0.0, "regime": regime_for_atr_pct(atr_pct),
            "win_prob_used": 0.0, "rr_planned": 0.0, "kelly_raw": 0.0,
            "explanation": "No position — HOLD signal",
        }

    regime = regime_for_atr_pct(atr_pct)
    stats = _load_stats()[regime]
    base_p = stats["hit_rate"]

    # Risk/Reward of THIS specific signal (planned, not historical)
    if signal == "BUY_CALL":
        risk = max(entry - stop, 0.01)
        reward = max(target - entry, 0.01)
    else:  # BUY_PUT
        risk = max(stop - entry, 0.01)
        reward = max(entry - target, 0.01)
    b = reward / risk

    # Adjust the historical hit rate by THIS signal's confidence:
    # blend regime base rate with confidence (which is also a probability estimate).
    conf_p = confidence / 100.0
    # Weight: 65% historical regime evidence, 35% current confidence.
    p = 0.65 * base_p + 0.35 * conf_p

    f_raw = kelly_fraction(p, b)
    f_safe = f_raw * _HALF_KELLY                    # half-Kelly for safety
    f_pct = max(_MIN_POSITION_PCT, min(_MAX_POSITION_PCT, f_safe * 100))

    dollars_per_10k = round(f_pct / 100.0 * 10_000, 2)

    if f_pct == 0:
        explain = f"Edge too small (p={p:.0%}, R:R={b:.2f}) — sit out"
    else:
        explain = (f"Half-Kelly: {regime} regime, blended win-prob {p:.0%}, "
                   f"R:R {b:.2f} → risk {f_pct:.1f}% of bankroll")

    return {
        "kelly_pct": round(f_pct, 2),
        "dollars_per_10k": dollars_per_10k,
        "regime": regime,
        "win_prob_used": round(p, 3),
        "rr_planned": round(b, 2),
        "kelly_raw": round(f_raw, 3),
        "explanation": explain,
    }
