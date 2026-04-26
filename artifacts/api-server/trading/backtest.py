"""
Oracle Backtester — replays historical bars through the 12-agent + Judge stack
and scores actual outcomes vs predictions.

Why this exists:
    Up to v7.0.2 every "improvement" to Oracle (threshold tweaks, soft-consensus
    tier, conviction-dominance multiplier, diversity-bonus curve) was validated
    only by spot-checking a handful of live signals. That's not measurement —
    that's vibes. This module gives every future change a real measuring stick:
    win rate, average return, profit factor, and a hard-vs-soft tier split,
    computed on real historical price action with no look-ahead.

How it works:
    1. Pull one long historical OHLCV series for the symbol/horizon
    2. Walk forward bar by bar. At each step, slice the dataframe to the bars
       up to and including the current one (so the agents only see what would
       have been visible at that moment) and feed it to compute_all_indicators
       + the 12 agents + JudgeAgent.decide.
    3. If the Judge fires CALL or PUT, look at the NEXT N bars (N = horizon's
       target_hit_bars) and score the actual outcome.
    4. Aggregate per (symbol, horizon) and overall.

Look-ahead safety:
    The only way look-ahead can leak is via external context fetched from
    yfinance for "now" (weekly_trend, spy_trend, macro_basket, etc.). We
    explicitly stub those to neutral defaults inside _build_indicators_for_bar
    so the backtest measures the CORE PRICE/VOLUME ENGINE's predictive power
    in isolation. Real-world performance with those filters wired up should
    be ≥ the backtest result, never worse.

Outcome scoring:
    For BUY_CALL  — win if return at exit > +0.5% over the lookforward window
    For BUY_PUT   — win if return at exit < -0.5% over the lookforward window
    HOLD signals are tracked but excluded from win-rate (no trade is taken)

CLI:
    python3 backtest.py --symbols SPY,AAPL,MSFT --horizons swing,position
    python3 backtest.py --symbols SPY --horizons swing --compare-soft

API endpoints (mounted from server.py):
    GET  /api/backtest/{symbol}?horizon=swing
    POST /api/backtest/bulk     body: {"symbols":[...], "horizons":[...]}
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

# Import every agent class + the Judge. We RE-INSTANTIATE them locally per
# backtest run so we don't share mutable state with the live server (MLAgent
# in particular has online-learned weights that would otherwise drift on
# every backtest call). The live server's `AGENTS` list is intentionally
# not imported.
from agents import (
    PriceActionAgent, TechnicalAgent, VolumeAgent,
    SentimentAgent, OptionsFlowAgent, MomentumAgent,
    RiskAgent, FearGreedAgent, PoliticalAgent, MLAgent,
    SectorRelativeStrengthAgent, MarketRegimeAgent, JudgeAgent,
    HORIZONS, get_horizon_config, DEFAULT_HORIZON, compute_htf_trend,
)
from indicators import compute_all_indicators


# Long-history fetch periods per horizon — chosen to give enough bars for
# both the indicator warmup (~60) and a meaningful number of test windows
# (target ≥150 evaluable bars after warmup + lookforward trimming).
_PERIOD_FOR_BACKTEST: dict[str, str] = {
    "intraday": "60d",   # 60d × ~78 5-min bars/day ≈ 4500 bars
    "day":      "60d",   # 60d × ~26 15-min bars/day ≈ 1500 bars
    "swing":    "6mo",   # 6mo × ~7 1h bars/day ≈ 900 bars
    "position": "2y",    # 2y × ~252 daily bars ≈ 500 bars
}

# Bars of history the agents need before indicators (EMAs, MACD, BB, ADX, ATR)
# stabilise. 60 covers the slowest indicator (EMA50) with margin.
_INDICATOR_WARMUP_BARS = 60

# Win threshold — we only count a directional move as a "win" if it cleared
# noise. 0.5% return is a reasonable floor: tighter and you're rewarding
# coin-flips, looser and you'd discard real edges.
_WIN_THRESHOLD_PCT = 0.5


def _fresh_agents() -> list:
    """Build a fresh agent list per backtest run — keeps MLAgent's online
    weights isolated from the live server's accumulated state."""
    return [
        PriceActionAgent(), TechnicalAgent(), VolumeAgent(),
        SentimentAgent(), OptionsFlowAgent(), MomentumAgent(),
        RiskAgent(), FearGreedAgent(), PoliticalAgent(),
        SectorRelativeStrengthAgent(),
        MarketRegimeAgent(),
        MLAgent(),  # last so it sees rs_score from SectorRS
    ]


def _build_indicators_for_bar(symbol: str, window_df: pd.DataFrame,
                              horizon_key: str) -> dict:
    """
    Replicates server.run_agents_sync's `ind` dict construction, but stubs
    every field that would normally come from a "now"-only external fetch
    (live news, fear/greed, weekly/spy trend from current yfinance, macro
    basket, sector rotation, earnings calendar, etc.) to a neutral default.

    This keeps the backtest free of look-ahead bias — the agents only see
    OHLCV data that existed at the simulated moment.
    """
    ind = compute_all_indicators(window_df)
    ind["_symbol"] = symbol
    ind["_horizon"] = horizon_key

    # Live-only context — stubbed to neutral. Means agents that depend on
    # these (Sentiment, FearGreed, Political, SectorRS, MarketRegime) will
    # tend to abstain to HOLD in backtest, which biases the result toward
    # the price/volume/momentum agents. That's fine — it's the core engine
    # we're measuring, and live performance should only be ≥ backtest.
    ind["_news"] = []
    ind["_info"] = {}
    ind["weekly_trend"] = {"dir": "flat", "strength": 0.0, "ema20": 0.0}
    ind["spy_trend"] = {"dir": "flat", "pct_from_ema50": 0.0, "change_1d": 0.0}
    ind["market_regime"] = {"label": "unknown", "vix": 0.0, "regime_score": 0.0}
    ind["macro_basket"] = {"macro_label": "unknown", "risk_on_score": 0,
                           "yield_curve_spread": 0.0,
                           "yield_curve_inverted": False}
    ind["sector_rotation"] = {}
    ind["fundamentals"] = {}
    ind["short_squeeze"] = {"score": 0.0}
    ind["earnings"] = {"days_until": 999, "has_event": False}

    # HTF trend — we CAN compute this safely from the same window by
    # downsampling. This matters because the Judge's HTF veto is one of the
    # top-3 alpha sources, and stubbing it to "flat" would systematically
    # let counter-trend signals through that the live system would block.
    ind["_htf_trend"] = _compute_htf_trend_from_window(window_df, horizon_key)

    # Discovered strategies — meta-learning artefact. Neutral default so we
    # measure the base agent stack, not the strategy mining layer (which
    # itself would need 15+ resolved snapshots to fire).
    ind["discovered_strategies"] = {"fired": [], "lean": "HOLD",
                                    "score": 0.0, "confidence_boost": 0.0}
    return ind


def _compute_htf_trend_from_window(window_df: pd.DataFrame,
                                   horizon_key: str) -> dict:
    """Downsample the visible window to a higher timeframe and compute the
    trend from THAT — same logic as the live _horizon_htf_trend but using
    only data the simulated 'now' would have access to. Zero look-ahead."""
    h = HORIZONS.get(horizon_key, HORIZONS[DEFAULT_HORIZON])
    target_interval = h.get("htf_interval", "1d")

    # Downsample mapping: how many of the current bars roll up into one HTF bar
    src_minutes = h.get("bar_minutes", 60)
    htf_minutes_map = {"1h": 60, "4h": 240, "1d": 24 * 60, "1wk": 7 * 24 * 60}
    htf_minutes = htf_minutes_map.get(target_interval, 24 * 60)
    factor = max(1, htf_minutes // max(1, src_minutes))

    if len(window_df) < factor * 25:  # need ≥25 HTF bars for the trend math
        return {"direction": 0, "strength": 0.0, "label": "unknown",
                "interval": target_interval}

    try:
        # Resample by integer factor — simple roll-up of OHLCV
        agg = {"Open": "first", "High": "max", "Low": "min",
               "Close": "last", "Volume": "sum"}
        # Use a synthetic group key based on integer position so we don't
        # depend on the index's freq (intraday data has gaps over weekends)
        group_keys = (np.arange(len(window_df)) // factor)
        htf = window_df.groupby(group_keys).agg(agg)
        result = compute_htf_trend(htf)
        result["interval"] = target_interval
        return result
    except Exception:
        return {"direction": 0, "strength": 0.0, "label": "unknown",
                "interval": target_interval}


def _run_judge_on_window(symbol: str, window_df: pd.DataFrame,
                         horizon_key: str, agents_list: list,
                         judge: JudgeAgent) -> Optional[dict]:
    """Run all agents + Judge on a single bar window. Returns the judgment
    dict or None on hard failure."""
    try:
        ind = _build_indicators_for_bar(symbol, window_df, horizon_key)
    except Exception:
        return None

    votes = []
    for agent in agents_list:
        try:
            vote = agent.analyze(window_df, ind)
        except Exception as e:
            vote = {"agent": agent.name, "emoji": "❓", "vote": "HOLD",
                    "confidence": 50.0, "reason": f"backtest err: {e}"}
        # No learned weights in backtest — pure base output. This intentionally
        # excludes the per-symbol/per-regime/per-horizon multipliers since
        # those depend on live predictions.db state that doesn't exist for
        # historical bars. Result: a stricter, more conservative test.
        vote["weight"] = 1.0
        votes.append(vote)
        # Mirror the live server's rs_score lift so MLAgent (running last)
        # sees the same indicator dict it would in production
        for k in ("rs_score", "rs_5d", "rs_20d", "sector_etf"):
            if k in vote and k not in ind:
                ind[k] = vote[k]

    try:
        return judge.decide(votes, ind)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# RESULT TYPES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeResult:
    """One scored prediction from the backtest walk."""
    bar_idx: int
    signal: str           # BUY_CALL | BUY_PUT | HOLD
    confidence: float
    entry_price: float
    exit_price: float
    return_pct: float     # signed % return at exit
    mfe_pct: float        # max favorable excursion (in trade direction)
    mae_pct: float        # max adverse excursion (in trade direction)
    win: bool
    soft_fire: bool       # did this fire on the soft-consensus tier?


@dataclass
class BacktestStats:
    """Aggregated stats for one (symbol, horizon) run."""
    symbol: str
    horizon: str
    bars_evaluated: int = 0
    n_call: int = 0
    n_put: int = 0
    n_hold: int = 0
    n_signals: int = 0          # call + put
    n_wins: int = 0
    n_losses: int = 0
    win_rate: float = 0.0       # wins / signals
    avg_return_pct: float = 0.0 # mean return across all signals (signed)
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0  # sum(wins) / abs(sum(losses)); inf if no losses
    sharpe_like: float = 0.0    # mean / stdev of per-trade returns

    # Soft-tier breakdown — proves whether v7.0.2's soft tier is +EV or -EV
    n_soft_signals: int = 0
    soft_win_rate: float = 0.0
    n_hard_signals: int = 0
    hard_win_rate: float = 0.0


def _aggregate(symbol: str, horizon: str,
               trades: list[TradeResult]) -> BacktestStats:
    """Roll a list of TradeResults into summary stats."""
    s = BacktestStats(symbol=symbol, horizon=horizon,
                      bars_evaluated=len(trades))
    sig_returns: list[float] = []
    win_returns: list[float] = []
    loss_returns: list[float] = []
    soft_wins = soft_total = hard_wins = hard_total = 0

    for t in trades:
        if t.signal == "HOLD":
            s.n_hold += 1
            continue
        if t.signal == "BUY_CALL":
            s.n_call += 1
        elif t.signal == "BUY_PUT":
            s.n_put += 1
        s.n_signals += 1

        # Return is sign-adjusted to the trade direction so we can compare
        # CALL and PUT trades on the same axis (positive = profitable).
        directional_ret = t.return_pct if t.signal == "BUY_CALL" else -t.return_pct
        sig_returns.append(directional_ret)
        if t.win:
            s.n_wins += 1
            win_returns.append(directional_ret)
        else:
            s.n_losses += 1
            loss_returns.append(directional_ret)

        if t.soft_fire:
            soft_total += 1
            if t.win:
                soft_wins += 1
        else:
            hard_total += 1
            if t.win:
                hard_wins += 1

    if s.n_signals:
        s.win_rate = round(s.n_wins / s.n_signals * 100, 1)
        s.avg_return_pct = round(statistics.mean(sig_returns), 3)
        if len(sig_returns) >= 2:
            sd = statistics.pstdev(sig_returns) or 1e-9
            s.sharpe_like = round(statistics.mean(sig_returns) / sd, 3)
    if win_returns:
        s.avg_win_pct = round(statistics.mean(win_returns), 3)
    if loss_returns:
        s.avg_loss_pct = round(statistics.mean(loss_returns), 3)
        gross_win = sum(win_returns)
        gross_loss = abs(sum(loss_returns)) or 1e-9
        s.profit_factor = round(gross_win / gross_loss, 3)
    elif win_returns:
        s.profit_factor = float("inf")

    s.n_soft_signals = soft_total
    s.n_hard_signals = hard_total
    if soft_total:
        s.soft_win_rate = round(soft_wins / soft_total * 100, 1)
    if hard_total:
        s.hard_win_rate = round(hard_wins / hard_total * 100, 1)
    return s


# ─────────────────────────────────────────────────────────────────────────────
# CORE WALK-FORWARD ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def backtest_symbol(symbol: str, horizon: str = DEFAULT_HORIZON,
                    max_bars: Optional[int] = None,
                    df_full: Optional[pd.DataFrame] = None
                    ) -> tuple[BacktestStats, list[TradeResult]]:
    """
    Walk-forward backtest a single symbol on one horizon.

    Args:
        symbol: ticker (e.g. "AAPL")
        horizon: one of HORIZONS.keys()
        max_bars: cap evaluable bars to this many (speeds up testing)
        df_full: optionally pre-fetched OHLCV. Lets the bulk runner share
            the network fetch across multiple horizons of the same symbol.

    Returns:
        (BacktestStats, list[TradeResult])
    """
    h = get_horizon_config(horizon)
    horizon_key = h["key"]
    lookforward = h["target_hit_bars"]

    if df_full is None:
        period = _PERIOD_FOR_BACKTEST.get(horizon_key, "1y")
        try:
            df_full = yf.Ticker(symbol).history(period=period,
                                                interval=h["interval"])
        except Exception as e:
            return BacktestStats(symbol=symbol, horizon=horizon_key), []

    if df_full is None or df_full.empty:
        return BacktestStats(symbol=symbol, horizon=horizon_key), []

    # Need enough bars for warmup + at least one evaluation window
    n = len(df_full)
    if n < _INDICATOR_WARMUP_BARS + lookforward + 5:
        return BacktestStats(symbol=symbol, horizon=horizon_key), []

    agents_list = _fresh_agents()
    judge = JudgeAgent()

    start_i = _INDICATOR_WARMUP_BARS
    end_i = n - lookforward - 1
    if max_bars is not None:
        # Sample evenly across the available range so we get coverage of the
        # whole period instead of just the most recent bars
        if end_i - start_i > max_bars:
            step = max((end_i - start_i) // max_bars, 1)
        else:
            step = 1
    else:
        step = 1

    trades: list[TradeResult] = []
    for i in range(start_i, end_i, step):
        window = df_full.iloc[: i + 1]
        judgment = _run_judge_on_window(symbol, window, horizon_key,
                                        agents_list, judge)
        if judgment is None:
            continue

        signal = judgment.get("signal", "HOLD")
        conf = float(judgment.get("confidence", 50))
        soft_fire = bool((judgment.get("evidence_pillars") or {})
                         .get("soft_fire", False))

        entry = float(window["Close"].iloc[-1])
        future = df_full.iloc[i + 1: i + 1 + lookforward]
        if future.empty:
            continue
        exit_price = float(future["Close"].iloc[-1])
        max_high = float(future["High"].max())
        min_low = float(future["Low"].min())

        if entry <= 0:
            continue
        ret_pct = (exit_price - entry) / entry * 100

        if signal == "BUY_CALL":
            mfe = (max_high - entry) / entry * 100
            mae = (min_low - entry) / entry * 100
            win = ret_pct > _WIN_THRESHOLD_PCT
        elif signal == "BUY_PUT":
            mfe = (entry - min_low) / entry * 100
            mae = -(max_high - entry) / entry * 100  # adverse for shorts
            win = ret_pct < -_WIN_THRESHOLD_PCT
        else:  # HOLD
            mfe = 0.0
            mae = 0.0
            win = False

        trades.append(TradeResult(
            bar_idx=i, signal=signal, confidence=conf,
            entry_price=round(entry, 2), exit_price=round(exit_price, 2),
            return_pct=round(ret_pct, 3),
            mfe_pct=round(mfe, 3), mae_pct=round(mae, 3),
            win=win, soft_fire=soft_fire,
        ))

    stats = _aggregate(symbol, horizon_key, trades)
    return stats, trades


def backtest_bulk(symbols: list[str], horizons: list[str],
                  max_bars: Optional[int] = None) -> dict:
    """
    Backtest every (symbol × horizon) combo and return both per-cell stats
    and an overall aggregate so the user can see the headline number.
    """
    cells: list[dict] = []
    all_trades: list[TradeResult] = []
    t0 = time.time()
    for sym in symbols:
        for h in horizons:
            stats, trades = backtest_symbol(sym, h, max_bars=max_bars)
            cells.append(asdict(stats))
            all_trades.extend(trades)
    overall = _aggregate("__ALL__", "__ALL__", all_trades)
    return {
        "elapsed_sec": round(time.time() - t0, 1),
        "symbols": symbols,
        "horizons": horizons,
        "max_bars": max_bars,
        "win_threshold_pct": _WIN_THRESHOLD_PCT,
        "indicator_warmup_bars": _INDICATOR_WARMUP_BARS,
        "cells": cells,
        "overall": asdict(overall),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _print_table(report: dict) -> None:
    """Pretty per-cell + overall table for terminal use."""
    print(f"\n=== Oracle Backtest — {report['elapsed_sec']}s ===")
    print(f"Win threshold: {report['win_threshold_pct']}%   "
          f"Indicator warmup: {report['indicator_warmup_bars']} bars")
    hdr = f"{'symbol':<7} {'horizon':<9} {'bars':>5} {'C':>3} {'P':>3} {'H':>4} " \
          f"{'sigs':>5} {'win%':>6} {'avgRet%':>8} {'PF':>6} {'sharpe':>7} " \
          f"{'softN':>6} {'soft%':>6} {'hardN':>6} {'hard%':>6}"
    print(hdr)
    print("-" * len(hdr))
    for c in report["cells"]:
        print(f"{c['symbol']:<7} {c['horizon']:<9} {c['bars_evaluated']:>5} "
              f"{c['n_call']:>3} {c['n_put']:>3} {c['n_hold']:>4} "
              f"{c['n_signals']:>5} {c['win_rate']:>5}% "
              f"{c['avg_return_pct']:>+7}% "
              f"{c['profit_factor']:>6} {c['sharpe_like']:>+7} "
              f"{c['n_soft_signals']:>6} {c['soft_win_rate']:>5}% "
              f"{c['n_hard_signals']:>6} {c['hard_win_rate']:>5}%")
    o = report["overall"]
    print("-" * len(hdr))
    print(f"{'OVERALL':<7} {'__ALL__':<9} {o['bars_evaluated']:>5} "
          f"{o['n_call']:>3} {o['n_put']:>3} {o['n_hold']:>4} "
          f"{o['n_signals']:>5} {o['win_rate']:>5}% "
          f"{o['avg_return_pct']:>+7}% "
          f"{o['profit_factor']:>6} {o['sharpe_like']:>+7} "
          f"{o['n_soft_signals']:>6} {o['soft_win_rate']:>5}% "
          f"{o['n_hard_signals']:>6} {o['hard_win_rate']:>5}%")


def main() -> int:
    ap = argparse.ArgumentParser(description="Oracle walk-forward backtester")
    ap.add_argument("--symbols", default="SPY,AAPL,MSFT,NVDA",
                    help="comma-sep list of tickers")
    ap.add_argument("--horizons", default="swing,position",
                    help="comma-sep list of horizons (intraday|day|swing|position)")
    ap.add_argument("--max-bars", type=int, default=120,
                    help="cap evaluable bars per cell to keep run-time sane")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of the table")
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    hs = [h.strip().lower() for h in args.horizons.split(",") if h.strip()]

    report = backtest_bulk(syms, hs, max_bars=args.max_bars)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_table(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
