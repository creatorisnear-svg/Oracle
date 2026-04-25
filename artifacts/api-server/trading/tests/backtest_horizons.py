"""Horizon-aware back-test for TradeSignal AI.

Runs the 9-agent pipeline historically across all four prediction horizons
(intraday / day / swing / position) and reports per-horizon accuracy so we
can validate the new short-term focus actually improves the hit rate on
options trades (calls / puts).

Each horizon is back-tested independently:
  - intraday  → 5-minute bars, 24-bar lookahead (~2h hold)
  - day       → 15-minute bars, 16-bar lookahead (~4h hold, 0DTE)
  - swing     → 1-hour bars, 30-bar lookahead (~5 trading days)
  - position  → 1-day bars, 7-bar lookahead (~7 trading days)

Reports for each horizon:
  signals fired, directional accuracy, target-hit rate,
  stop-first rate, average signed move, average forecast MAE.
"""
import sys, os, statistics
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yfinance as yf
import numpy as np
from indicators import compute_all_indicators
from agents import (
    PriceActionAgent, TechnicalAgent, VolumeAgent, SentimentAgent,
    OptionsFlowAgent, MomentumAgent, RiskAgent, FearGreedAgent,
    PoliticalAgent, JudgeAgent, HORIZONS,
)

AGENTS = [PriceActionAgent(), TechnicalAgent(), VolumeAgent(), SentimentAgent(),
          OptionsFlowAgent(), MomentumAgent(), RiskAgent(), FearGreedAgent(),
          PoliticalAgent()]
JUDGE = JudgeAgent()


def _run_agents(df_slice, horizon_key: str):
    ind = compute_all_indicators(df_slice)
    ind["_horizon"] = horizon_key  # critical — JudgeAgent reads this
    votes = []
    for a in AGENTS:
        try:
            v = a.analyze(df_slice, ind)
        except Exception as e:
            v = {"agent": a.name, "vote": "HOLD", "confidence": 50, "reason": f"err:{e}"}
        votes.append(v)
    return JUDGE.decide(votes, ind), ind


def _fetch_history(symbol: str, period: str, interval: str):
    """Fetch the longest history yfinance allows for the given interval."""
    # yfinance limits intraday lookback (5m → 60d, 15m → 60d, 1h → 730d)
    if interval in ("5m", "15m"):
        return yf.Ticker(symbol).history(period="60d", interval=interval)
    if interval == "1h":
        return yf.Ticker(symbol).history(period="60d", interval="1h")
    return yf.Ticker(symbol).history(period="2y", interval="1d")


def backtest_horizon(symbol: str, horizon_key: str, num_tests: int = 15):
    cfg = HORIZONS[horizon_key]
    interval = cfg["interval"]
    forecast_bars = cfg["forecast_bars"]
    label = cfg["label"]

    full = _fetch_history(symbol, cfg["period"], interval)
    if full.empty or len(full) < 80:
        return None
    full = full.dropna()

    # Need enough warm-up bars for indicators (>=60) and forecast lookahead
    end_idx = len(full) - forecast_bars - 1
    start_idx = max(60, end_idx - num_tests * 6)
    if end_idx <= start_idx:
        return None
    test_idxs = np.linspace(start_idx, end_idx, num_tests, dtype=int)

    results = []
    for i in test_idxs:
        df_slice = full.iloc[: i + 1].copy()
        future = full.iloc[i + 1 : i + 1 + forecast_bars].copy()
        if len(future) < forecast_bars:
            continue
        try:
            j, _ind = _run_agents(df_slice, horizon_key)
        except Exception:
            continue
        if j["signal"] == "HOLD":
            continue

        entry, target, stop, signal = j["entry_price"], j["target_price"], j["stop_loss"], j["signal"]
        ahighs = future["High"].values
        alows = future["Low"].values
        aclose = float(future["Close"].values[-1])

        if signal == "BUY_CALL":
            direction_ok = aclose > entry
            target_hit = float(ahighs.max()) >= target
            stop_first = False
            for h, l in zip(ahighs, alows):
                if l <= stop:
                    stop_first = True; break
                if h >= target:
                    break
            move_pct = (aclose - entry) / entry * 100
        else:
            direction_ok = aclose < entry
            target_hit = float(alows.min()) <= target
            stop_first = False
            for h, l in zip(ahighs, alows):
                if h >= stop:
                    stop_first = True; break
                if l <= target:
                    break
            move_pct = -(aclose - entry) / entry * 100

        results.append({
            "signal": signal, "conf": j["confidence"],
            "direction_ok": direction_ok, "target_hit": target_hit,
            "stop_first": stop_first, "move_pct": move_pct,
        })

    if not results:
        return {"symbol": symbol, "horizon": horizon_key, "label": label,
                "n": 0, "dir_acc": None, "target_rate": None,
                "stop_first_rate": None, "avg_move": None, "avg_conf": None}

    n = len(results)
    return {
        "symbol": symbol, "horizon": horizon_key, "label": label, "n": n,
        "dir_acc": sum(1 for r in results if r["direction_ok"]) / n * 100,
        "target_rate": sum(1 for r in results if r["target_hit"]) / n * 100,
        "stop_first_rate": sum(1 for r in results if r["stop_first"]) / n * 100,
        "avg_move": statistics.mean(r["move_pct"] for r in results),
        "avg_conf": statistics.mean(r["conf"] for r in results),
    }


def main():
    symbols = sys.argv[1:] or ["AAPL", "NVDA", "MSFT", "TSLA", "SPY", "AMZN", "META"]
    horizon_keys = ["intraday", "day", "swing", "position"]

    grand: dict[str, list[dict]] = {h: [] for h in horizon_keys}

    for sym in symbols:
        print(f"\n=== {sym} ===")
        for h in horizon_keys:
            r = backtest_horizon(sym, h)
            if r and r["n"] > 0:
                grand[h].append(r)
                print(f"  {r['label']:<20s} n={r['n']:>2d}  "
                      f"dir={r['dir_acc']:5.1f}%  "
                      f"target={r['target_rate']:5.1f}%  "
                      f"stop1st={r['stop_first_rate']:5.1f}%  "
                      f"avgMove={r['avg_move']:+5.2f}%  "
                      f"avgConf={r['avg_conf']:5.1f}%")
            else:
                print(f"  {HORIZONS[h]['label']:<20s} no signals fired")

    print(f"\n{'='*70}\nOVERALL (per horizon, weighted by signal count)\n{'='*70}")
    for h in horizon_keys:
        rs = grand[h]
        if not rs:
            print(f"  {HORIZONS[h]['label']:<20s} no signals fired across symbols")
            continue
        total = sum(r["n"] for r in rs)
        wd = sum(r["dir_acc"] * r["n"] for r in rs) / total
        wt = sum(r["target_rate"] * r["n"] for r in rs) / total
        ws = sum(r["stop_first_rate"] * r["n"] for r in rs) / total
        wm = sum(r["avg_move"] * r["n"] for r in rs) / total
        wc = sum(r["avg_conf"] * r["n"] for r in rs) / total
        print(f"  {HORIZONS[h]['label']:<20s} n={total:>3d}  "
              f"dir={wd:5.1f}%  target={wt:5.1f}%  "
              f"stop1st={ws:5.1f}%  avgMove={wm:+5.2f}%  "
              f"avgConf={wc:5.1f}%")


if __name__ == "__main__":
    main()
