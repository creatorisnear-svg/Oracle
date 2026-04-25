"""Run the back-test, bucket signals by volatility regime, compute hit rate + R:R,
and save to ../regime_stats.json for the Kelly sizer to use."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import yfinance as yf
from indicators import compute_all_indicators
from agents import (PriceActionAgent, TechnicalAgent, VolumeAgent, SentimentAgent,
                    OptionsFlowAgent, MomentumAgent, RiskAgent, FearGreedAgent,
                    PoliticalAgent, JudgeAgent)

AGENTS = [PriceActionAgent(), TechnicalAgent(), VolumeAgent(), SentimentAgent(),
          OptionsFlowAgent(), MomentumAgent(), RiskAgent(), FearGreedAgent(),
          PoliticalAgent()]
JUDGE = JudgeAgent()
SYMBOLS = ["AAPL", "NVDA", "MSFT", "TSLA", "SPY", "AMZN", "META", "GOOGL", "QQQ"]
HORIZON = 7
TESTS_PER_SYM = 25


def regime_of(atr_pct):
    if atr_pct < 1.5: return "low_vol"
    if atr_pct > 4.0: return "high_vol"
    return "normal"


def collect():
    buckets = {"low_vol": [], "normal": [], "high_vol": []}
    for sym in SYMBOLS:
        try:
            full = yf.Ticker(sym).history(period="2y", interval="1d").dropna()
        except Exception:
            continue
        if len(full) < 100: continue
        end_idx = len(full) - HORIZON - 1
        idxs = np.linspace(60, end_idx, TESTS_PER_SYM, dtype=int)
        for i in idxs:
            df_slice = full.iloc[: i + 1]
            future = full.iloc[i + 1 : i + 1 + HORIZON]
            if len(future) < HORIZON: continue
            try:
                ind = compute_all_indicators(df_slice)
                votes = []
                for a in AGENTS:
                    try: votes.append(a.analyze(df_slice, ind))
                    except Exception: pass
                j = JUDGE.decide(votes, ind)
            except Exception: continue
            if j["signal"] == "HOLD": continue

            atr_pct = (ind.get("atr14", 0) / ind.get("price", 1)) * 100
            reg = regime_of(atr_pct)
            entry, target, stop = j["entry_price"], j["target_price"], j["stop_loss"]
            highs, lows = future["High"].values, future["Low"].values

            # Walk forward: did target or stop get hit first?
            outcome_R = None
            if j["signal"] == "BUY_CALL":
                R = (target - entry) / max(entry - stop, 0.01)
                for h, l in zip(highs, lows):
                    if l <= stop: outcome_R = -1.0; break
                    if h >= target: outcome_R = R; break
                if outcome_R is None:
                    outcome_R = (future["Close"].values[-1] - entry) / max(entry - stop, 0.01)
            else:
                R = (entry - target) / max(stop - entry, 0.01)
                for h, l in zip(highs, lows):
                    if h >= stop: outcome_R = -1.0; break
                    if l <= target: outcome_R = R; break
                if outcome_R is None:
                    outcome_R = (entry - future["Close"].values[-1]) / max(stop - entry, 0.01)
            buckets[reg].append({"R": outcome_R, "won": outcome_R > 0,
                                 "RR_planned": R, "conf": j["confidence"]})
    return buckets


def summarize(buckets):
    out = {}
    for reg, trades in buckets.items():
        if not trades:
            out[reg] = {"hit_rate": 0.45, "avg_RR": 1.2, "n": 0}
            continue
        wins = [t for t in trades if t["won"]]
        losses = [t for t in trades if not t["won"]]
        hit_rate = len(wins) / len(trades)
        avg_win_R = float(np.mean([t["R"] for t in wins])) if wins else 0
        avg_loss_R = float(np.mean([abs(t["R"]) for t in losses])) if losses else 1.0
        avg_RR = avg_win_R / avg_loss_R if avg_loss_R > 0 else 1.0
        out[reg] = {
            "hit_rate": round(hit_rate, 3),
            "avg_RR": round(avg_RR, 3),
            "n": len(trades),
            "avg_win_R": round(avg_win_R, 3),
            "avg_loss_R": round(avg_loss_R, 3),
        }
    return out


if __name__ == "__main__":
    print("Running back-test across", SYMBOLS, "...")
    buckets = collect()
    stats = summarize(buckets)
    out_path = os.path.join(os.path.dirname(__file__), "..", "regime_stats.json")
    with open(out_path, "w") as f:
        json.dump(stats, f, indent=2)
    print("\nRegime stats:")
    for reg, s in stats.items():
        print(f"  {reg:10s}  n={s['n']:4d}  hit_rate={s['hit_rate']:.1%}  avg_RR={s['avg_RR']:.2f}")
    print(f"\nSaved → {os.path.abspath(out_path)}")
