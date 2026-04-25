"""Back-test the 9-agent prediction system against actual historical price moves.

For each test date T:
  1. Build a dataset that contains ONLY data available up to T  2. Compute indicators and run all 9 agents
  3. Get the JudgeAgent's signal + forecast_line  4. Compare the forecast against the ACTUAL prices that followed
  5. Report directional accuracy, hit-rate of the target, and forecast error
"""
import sys, os, statistics, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yfinance as yf
import pandas as pd
import numpy as np
from indicators import compute_all_indicators
from agents import (PriceActionAgent, TechnicalAgent, VolumeAgent, SentimentAgent,
                    OptionsFlowAgent, MomentumAgent, RiskAgent, FearGreedAgent,
                    PoliticalAgent, JudgeAgent)

AGENTS = [PriceActionAgent(), TechnicalAgent(), VolumeAgent(), SentimentAgent(),
          OptionsFlowAgent(), MomentumAgent(), RiskAgent(), FearGreedAgent(),
          PoliticalAgent()]
JUDGE = JudgeAgent()


def run_agents(df_slice):
    ind = compute_all_indicators(df_slice)
    votes = []
    for a in AGENTS:
        try:
            v = a.analyze(df_slice, ind)
        except Exception as e:
            v = {"agent": a.name, "vote": "HOLD", "confidence": 50, "reason": f"err:{e}"}
        votes.append(v)
    return JUDGE.decide(votes, ind), ind


def backtest_symbol(symbol, lookback_days=180, horizon=7, num_tests=20):
    """Run num_tests back-tests on `symbol` over the last `lookback_days`."""
    print(f"\n{'='*60}\n{symbol}\n{'='*60}")
    full = yf.Ticker(symbol).history(period="2y", interval="1d")
    if full.empty or len(full) < 100:
        print(f"  no data"); return None
    full = full.dropna()

    # Pick test indices spaced through the lookback window (must leave horizon ahead)
    end_idx = len(full) - horizon - 1
    start_idx = max(60, end_idx - lookback_days)
    test_idxs = np.linspace(start_idx, end_idx, num_tests, dtype=int)

    results = []
    for i in test_idxs:
        df_slice = full.iloc[: i + 1].copy()
        future = full.iloc[i + 1 : i + 1 + horizon].copy()
        if len(future) < horizon:
            continue
        try:
            j, ind = run_agents(df_slice)
        except Exception as e:
            print(f"  skip {full.index[i].date()}: {e}"); continue

        signal = j["signal"]
        if signal == "HOLD":
            continue

        entry = j["entry_price"]
        target = j["target_price"]
        stop = j["stop_loss"]
        conf = j["confidence"]
        forecast = j.get("forecast_line", [])

        actual_prices = future["Close"].values
        actual_highs = future["High"].values
        actual_lows = future["Low"].values
        actual_end = float(actual_prices[-1])
        actual_max = float(actual_highs.max())
        actual_min = float(actual_lows.min())

        # Direction correct?
        if signal == "BUY_CALL":
            direction_ok = actual_end > entry
            target_hit = actual_max >= target
            stop_hit_first = False
            for h, l in zip(actual_highs, actual_lows):
                if l <= stop:
                    stop_hit_first = True
                    break
                if h >= target:
                    break
        else:  # BUY_PUT
            direction_ok = actual_end < entry
            target_hit = actual_min <= target
            stop_hit_first = False
            for h, l in zip(actual_highs, actual_lows):
                if h >= stop:
                    stop_hit_first = True
                    break
                if l <= target:
                    break

        # Forecast vs actual MAE (ignore the anchor point)
        if len(forecast) > 1 and len(actual_prices) > 0:
            f_vals = [p["value"] for p in forecast[1: len(actual_prices) + 1]]
            a_vals = list(actual_prices[: len(f_vals)])
            mae = float(np.mean([abs(f - a) for f, a in zip(f_vals, a_vals)])) if f_vals else None
            mae_pct = (mae / entry * 100) if mae is not None else None
        else:
            mae_pct = None

        actual_move_pct = (actual_end - entry) / entry * 100
        if signal == "BUY_PUT":
            actual_move_pct = -actual_move_pct  # signed by intent

        results.append({
            "date": str(full.index[i].date()),
            "signal": signal,
            "conf": conf,
            "entry": round(entry, 2),
            "target": round(target, 2),
            "actual_end": round(actual_end, 2),
            "actual_move_pct": round(actual_move_pct, 2),
            "direction_ok": direction_ok,
            "target_hit": target_hit,
            "stop_first": stop_hit_first,
            "mae_pct": round(mae_pct, 2) if mae_pct is not None else None,
        })

    if not results:
        print(f"  no signals fired"); return None

    n = len(results)
    dir_acc = sum(1 for r in results if r["direction_ok"]) / n * 100
    target_rate = sum(1 for r in results if r["target_hit"]) / n * 100
    stop_first_rate = sum(1 for r in results if r["stop_first"]) / n * 100
    avg_move = statistics.mean(r["actual_move_pct"] for r in results)
    avg_conf = statistics.mean(r["conf"] for r in results)
    avg_mae_pct = statistics.mean(r["mae_pct"] for r in results if r["mae_pct"] is not None)

    # Confidence calibration: are higher-confidence signals actually more accurate?
    high_conf = [r for r in results if r["conf"] >= 75]
    low_conf  = [r for r in results if r["conf"] < 75]
    high_acc = sum(1 for r in high_conf if r["direction_ok"]) / len(high_conf) * 100 if high_conf else None
    low_acc  = sum(1 for r in low_conf  if r["direction_ok"]) / len(low_conf)  * 100 if low_conf  else None

    print(f"  signals fired:       {n}")
    print(f"  avg confidence:      {avg_conf:.1f}%")
    print(f"  directional accuracy:{dir_acc:5.1f}%   (>50% = better than coin flip)")
    print(f"  target hit rate:     {target_rate:5.1f}%")
    print(f"  stop hit first:      {stop_first_rate:5.1f}%")
    print(f"  avg signed move:     {avg_move:+.2f}% (positive = went the predicted way)")
    print(f"  forecast MAE:        {avg_mae_pct:.2f}% of entry price")
    if high_acc is not None and low_acc is not None:
        print(f"  high-conf (>=75%):   {high_acc:.1f}% accurate ({len(high_conf)} signals)")
        print(f"  low-conf  (<75%):    {low_acc:.1f}% accurate ({len(low_conf)} signals)")
    return {
        "symbol": symbol, "n": n, "dir_acc": dir_acc, "target_rate": target_rate,
        "stop_first_rate": stop_first_rate, "avg_move": avg_move, "avg_conf": avg_conf,
        "avg_mae_pct": avg_mae_pct, "results": results,
    }


if __name__ == "__main__":
    syms = sys.argv[1:] or ["AAPL", "NVDA", "MSFT", "TSLA", "SPY", "AMZN", "META"]
    summary = []
    for s in syms:
        r = backtest_symbol(s)
        if r: summary.append(r)

    if summary:
        print(f"\n{'='*60}\nOVERALL\n{'='*60}")
        total_n = sum(s['n'] for s in summary)
        weighted_dir = sum(s['dir_acc'] * s['n'] for s in summary) / total_n
        weighted_target = sum(s['target_rate'] * s['n'] for s in summary) / total_n
        weighted_stop = sum(s['stop_first_rate'] * s['n'] for s in summary) / total_n
        weighted_mae = sum(s['avg_mae_pct'] * s['n'] for s in summary) / total_n
        print(f"  total signals:       {total_n}")
        print(f"  directional acc:     {weighted_dir:.1f}%")
        print(f"  target hit:          {weighted_target:.1f}%")
        print(f"  stop hit first:      {weighted_stop:.1f}%")
        print(f"  forecast MAE:        {weighted_mae:.2f}% of price")
