"""Generate per-stock historical track record for the model.

For each stock, replays the 9-agent system over 2 years of history,
records every signal vs eventual outcome, and writes a JSON file the
API consumes to show users the model's REAL track record per ticker.

Usage:  python3 tests/compute_track_record.py [SYMBOLS...]
Output: artifacts/api-server/trading/track_record.json
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings
warnings.filterwarnings("ignore")
import numpy as np
import yfinance as yf
from indicators import compute_all_indicators
from agents import (
    PriceActionAgent, TechnicalAgent, VolumeAgent, SentimentAgent,
    OptionsFlowAgent, MomentumAgent, RiskAgent, FearGreedAgent,
    PoliticalAgent, JudgeAgent,
)

DEFAULT_SYMBOLS = ["AAPL", "NVDA", "MSFT", "TSLA", "SPY", "AMZN", "META", "GOOGL", "QQQ"]
SAMPLES_PER_STOCK = 30   # ~30 sample points across 2 years
HORIZON_DAYS = 7         # one trading week ahead


def evaluate(symbol: str, agents, judge) -> dict:
    full = yf.Ticker(symbol).history(period="2y", interval="1d").dropna()
    if len(full) < 100:
        return {"signals": 0, "wins": 0, "losses": 0, "hit_rate": None, "samples": 0}

    end = len(full) - HORIZON_DAYS - 1
    wins = losses = 0
    signed_moves = []

    for i in np.linspace(60, end, SAMPLES_PER_STOCK, dtype=int):
        df_s = full.iloc[: i + 1]
        fut = full.iloc[i + 1 : i + 1 + HORIZON_DAYS]
        if len(fut) < HORIZON_DAYS:
            continue
        try:
            ind = compute_all_indicators(df_s)
            votes = [a.analyze(df_s, ind) for a in agents]
            j = judge.decide(votes, ind)
        except Exception:
            continue
        if j["signal"] == "HOLD":
            continue
        end_p = float(fut["Close"].values[-1])
        entry = float(j["entry_price"])
        if j["signal"] == "BUY_CALL":
            won = end_p > entry
            signed_moves.append((end_p - entry) / entry * 100)
        else:
            won = end_p < entry
            signed_moves.append((entry - end_p) / entry * 100)
        if won:
            wins += 1
        else:
            losses += 1

    n = wins + losses
    return {
        "signals": n,
        "wins": wins,
        "losses": losses,
        "hit_rate": round(wins / n * 100, 1) if n else None,
        "avg_signed_move_pct": round(float(np.mean(signed_moves)), 2) if signed_moves else None,
        "samples": SAMPLES_PER_STOCK,
    }


def main():
    symbols = sys.argv[1:] or DEFAULT_SYMBOLS
    agents = [
        PriceActionAgent(), TechnicalAgent(), VolumeAgent(), SentimentAgent(),
        OptionsFlowAgent(), MomentumAgent(), RiskAgent(), FearGreedAgent(),
        PoliticalAgent(),
    ]
    judge = JudgeAgent()

    out = {"per_stock": {}, "overall": {}}
    total_w = total_l = 0
    print("Computing 2-year track record per stock...")
    for sym in symbols:
        try:
            r = evaluate(sym, agents, judge)
            out["per_stock"][sym] = r
            total_w += r["wins"]; total_l += r["losses"]
            hr = f"{r['hit_rate']}%" if r['hit_rate'] is not None else "n/a"
            print(f"  {sym:6s} {r['signals']:3d} signals  hit-rate {hr}")
        except Exception as e:
            print(f"  {sym:6s} ERROR {e}")
            out["per_stock"][sym] = {"signals": 0, "hit_rate": None, "error": str(e)}

    n = total_w + total_l
    out["overall"] = {
        "signals": n,
        "wins": total_w,
        "losses": total_l,
        "hit_rate": round(total_w / n * 100, 1) if n else None,
    }
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "track_record.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nOverall: {total_w}/{n} = {out['overall']['hit_rate']}%")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
