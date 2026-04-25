"""
TradeSignal AI — FastAPI backend v4
9 Agents: PriceAction, Technical, Volume, Sentiment, OptionsFlow, Momentum,
          Risk, FearGreed, Political  +  Judge (fires at 6/9 consensus)
New: /api/fear-greed  /api/political-news  /api/accuracy  /api/accuracy/{symbol}
"""
import asyncio
import concurrent.futures
import json
import logging
import os
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
import yfinance as yf
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from agents import (
    PriceActionAgent, TechnicalAgent, VolumeAgent,
    SentimentAgent, OptionsFlowAgent, MomentumAgent,
    RiskAgent, FearGreedAgent, PoliticalAgent, JudgeAgent,
    HORIZONS, get_horizon_config, DEFAULT_HORIZON, compute_htf_trend,
)
from indicators import compute_all_indicators, safe_float
from learning import LearningSystem

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8080))


def _sanitize(obj):
    """Recursively convert numpy scalars to Python natives for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items() if not k.startswith("_") and not callable(v)}
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    if isinstance(obj, np.ndarray):
        return None
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


AGENTS = [
    PriceActionAgent(), TechnicalAgent(), VolumeAgent(),
    SentimentAgent(), OptionsFlowAgent(), MomentumAgent(),
    RiskAgent(), FearGreedAgent(), PoliticalAgent(),
]
JUDGE = JudgeAgent()
LEARNING = LearningSystem()

# Meta-learning: indicator-snapshot logging, per-regime weighting,
# and AI-discovered strategy mining. See trading/meta_learning.py.
import meta_learning  # noqa: E402
meta_learning.bootstrap()

# Live price cache  {symbol → {price, change_pct, ts, news}}
_LIVE_CACHE: dict[str, dict] = {}
# WebSocket clients  {symbol → set of WebSocket}
_WS_CLIENTS: dict[str, set] = {}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _sync_fetch_live(symbol: str) -> dict:
    """Synchronous price + news fetch (runs in thread executor)."""
    try:
        t = yf.Ticker(symbol)
        info = t.fast_info
        price = safe_float(getattr(info, "last_price", None) or
                           getattr(info, "regularMarketPrice", None))
        prev = safe_float(getattr(info, "previous_close", None) or price)
        change_pct = (price - prev) / prev * 100 if prev > 0 else 0.0

        news = []
        try:
            for n in (t.news or [])[:10]:
                content = n.get("content", {}) or {}
                news.append({
                    "title": content.get("title") or n.get("title", ""),
                    "summary": content.get("summary") or n.get("summary", ""),
                    "source": ((content.get("provider") or {}).get("displayName")
                               or n.get("publisher", "Yahoo Finance")),
                    "url": ((content.get("canonicalUrl") or {}).get("url")
                            or n.get("link", "#")),
                    "published_at": str(content.get("pubDate") or
                                       n.get("providerPublishTime", "")),
                    "category": "financial",
                })
        except Exception:
            pass

        return {
            "symbol": symbol,
            "price": round(price, 2),
            "change_pct": round(change_pct, 3),
            "ts": int(time.time()),
            "market_state": str(getattr(info, "market_state", "REGULAR") or "REGULAR"),
            "news": news,
        }
    except Exception:
        cached = _LIVE_CACHE.get(symbol, {})
        return {**cached, "ts": int(time.time()), "price": cached.get("price", 0),
                "change_pct": cached.get("change_pct", 0), "news": cached.get("news", [])}


_DF_CACHE: dict[tuple, tuple] = {}  # (sym, period, interval) -> (df, info, ts)
_DF_CACHE_TTL = 30  # seconds — multiple WS clients in the same window share the fetch


def get_df(symbol: str, period: str = "3mo", interval: str = "1d"):
    key = (symbol, period, interval)
    cached = _DF_CACHE.get(key)
    if cached and (time.time() - cached[2]) < _DF_CACHE_TTL:
        return cached[0], cached[1]
    t = yf.Ticker(symbol)
    df = t.history(period=period, interval=interval)
    if df.empty:
        raise ValueError(f"No data for {symbol}")
    info = {}
    try:
        info = t.info
    except Exception:
        pass
    _DF_CACHE[key] = (df, info, time.time())
    return df, info


def _weekly_trend(symbol: str) -> dict:
    """Higher-timeframe trend filter: weekly EMA20 slope + price-vs-EMA20.
    Returns {dir: 'up'|'down'|'flat', strength: float, ema20: float}.
    Cached 1 h since weekly data only meaningfully changes once per week."""
    key = (symbol, "weekly_trend")
    cached = _DF_CACHE.get(key)
    if cached and (time.time() - cached[2]) < 3600:
        return cached[0]
    try:
        wdf = yf.Ticker(symbol).history(period="2y", interval="1wk")
        if wdf.empty or len(wdf) < 25:
            result = {"dir": "flat", "strength": 0.0, "ema20": 0.0}
        else:
            closes = wdf["Close"].values
            ema20 = pd.Series(closes).ewm(span=20).mean().values
            price = float(closes[-1])
            e_now = float(ema20[-1])
            e_prev = float(ema20[-5]) if len(ema20) >= 5 else float(ema20[-1])
            slope_pct = (e_now - e_prev) / e_prev * 100 if e_prev > 0 else 0.0
            above = price > e_now
            if slope_pct > 0.3 and above:
                direction = "up"
            elif slope_pct < -0.3 and not above:
                direction = "down"
            else:
                direction = "flat"
            result = {"dir": direction, "strength": float(abs(slope_pct)), "ema20": e_now}
    except Exception:
        result = {"dir": "flat", "strength": 0.0, "ema20": 0.0}
    _DF_CACHE[key] = (result, None, time.time())
    return result


def _horizon_htf_trend(symbol: str, horizon_key: str) -> dict:
    """Per-horizon higher-timeframe trend.
    Cached for 5 minutes per (symbol, horizon)."""
    h_cfg = HORIZONS.get(horizon_key, HORIZONS[DEFAULT_HORIZON])
    htf_period = h_cfg.get("htf_period", "6mo")
    htf_interval = h_cfg.get("htf_interval", "1d")
    key = (symbol, "htf", htf_period, htf_interval)
    cached = _DF_CACHE.get(key)
    if cached and (time.time() - cached[2]) < 300:
        return cached[0]
    try:
        hdf = yf.Ticker(symbol).history(period=htf_period, interval=htf_interval)
        result = compute_htf_trend(hdf)
        result["interval"] = htf_interval
    except Exception:
        result = {"direction": 0, "strength": 0.0, "label": "unknown",
                  "interval": htf_interval}
    _DF_CACHE[key] = (result, None, time.time())
    return result


def _spy_trend() -> dict:
    """Index-context filter: SPY daily SuperTrend + EMA50 slope.
    Returns {dir: 'up'|'down'|'flat', pct_from_ema50: float}.
    Cached 5 min since this only needs to be computed once across symbols."""
    key = ("__SPY__", "spy_trend")
    cached = _DF_CACHE.get(key)
    if cached and (time.time() - cached[2]) < 300:
        return cached[0]
    try:
        sdf = yf.Ticker("SPY").history(period="3mo", interval="1d")
        if sdf.empty or len(sdf) < 60:
            result = {"dir": "flat", "pct_from_ema50": 0.0, "change_1d": 0.0}
        else:
            closes = sdf["Close"].values
            ema50 = pd.Series(closes).ewm(span=50).mean().values
            price = float(closes[-1])
            prev = float(closes[-2]) if len(closes) >= 2 else price
            e_now = float(ema50[-1])
            e_prev = float(ema50[-10]) if len(ema50) >= 10 else float(ema50[-1])
            slope_pct = (e_now - e_prev) / e_prev * 100 if e_prev > 0 else 0.0
            pct_from = (price - e_now) / e_now * 100 if e_now > 0 else 0.0
            change_1d = (price - prev) / prev * 100 if prev > 0 else 0.0
            if slope_pct > 0.4 and pct_from > -1.0:
                direction = "up"
            elif slope_pct < -0.4 and pct_from < 1.0:
                direction = "down"
            else:
                direction = "flat"
            result = {"dir": direction, "pct_from_ema50": pct_from, "change_1d": change_1d}
    except Exception:
        result = {"dir": "flat", "pct_from_ema50": 0.0, "change_1d": 0.0}
    _DF_CACHE[key] = (result, None, time.time())
    return result


def _macro_basket() -> dict:
    """Fetch DXY, oil, 10y/2y yields, BTC, TLT (long bonds), gold. Cached 10 min.
    Used by Judge to detect risk-on vs risk-off macro context."""
    key = ("__MACRO__", "basket")
    cached = _DF_CACHE.get(key)
    if cached and (time.time() - cached[2]) < 600:
        return cached[0]

    def _trend(ticker: str, period: str = "1mo") -> dict:
        try:
            d = yf.Ticker(ticker).history(period=period, interval="1d")
            if d.empty or len(d) < 5:
                return {"value": 0.0, "change_5d": 0.0, "dir": "flat"}
            closes = d["Close"].values
            v = float(closes[-1])
            prev = float(closes[-min(5, len(closes))])
            chg = (v - prev) / prev * 100 if prev else 0.0
            direction = "up" if chg > 0.5 else "down" if chg < -0.5 else "flat"
            return {"value": round(v, 2), "change_5d": round(chg, 2), "dir": direction}
        except Exception:
            return {"value": 0.0, "change_5d": 0.0, "dir": "flat"}

    try:
        dxy = _trend("DX-Y.NYB")
        oil = _trend("CL=F")
        tnx = _trend("^TNX")          # 10-year yield
        irx = _trend("^IRX")          # 13-week yield (proxy for short end)
        btc = _trend("BTC-USD")
        tlt = _trend("TLT")           # long-bond ETF
        gold = _trend("GC=F")
        # Yield-curve spread (10y - short)
        curve_spread = round(tnx["value"] - irx["value"], 2)
        # Risk-on score: oil up + BTC up + bonds down + DXY flat-ish = risk-on
        risk_on_score = 0
        risk_on_score += 1 if oil["dir"] == "up" else (-1 if oil["dir"] == "down" else 0)
        risk_on_score += 1 if btc["dir"] == "up" else (-1 if btc["dir"] == "down" else 0)
        risk_on_score += 1 if tlt["dir"] == "down" else (-1 if tlt["dir"] == "up" else 0)
        risk_on_score += 1 if dxy["dir"] == "down" else (-1 if dxy["dir"] == "up" else 0)
        # -4 .. +4 → label
        if risk_on_score >= 2:
            macro_label = "risk-on"
        elif risk_on_score <= -2:
            macro_label = "risk-off"
        else:
            macro_label = "mixed"
        result = {
            "dxy": dxy, "oil": oil, "tnx": tnx, "irx": irx,
            "btc": btc, "tlt": tlt, "gold": gold,
            "yield_curve_spread": curve_spread,
            "yield_curve_inverted": curve_spread < 0,
            "risk_on_score": risk_on_score,
            "macro_label": macro_label,
        }
    except Exception as e:
        result = {"macro_label": "unknown", "risk_on_score": 0,
                  "yield_curve_spread": 0.0, "_error": str(e)}
    _DF_CACHE[key] = (result, None, time.time())
    return result


def _sector_rotation() -> dict:
    """5-day % change of the 11 SPDR sector ETFs. Tells you which sectors are leading.
    Cached 10 min."""
    key = ("__MACRO__", "sectors")
    cached = _DF_CACHE.get(key)
    if cached and (time.time() - cached[2]) < 600:
        return cached[0]
    sectors = {
        "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
        "XLV": "Healthcare", "XLY": "Consumer Discretionary",
        "XLP": "Consumer Staples", "XLI": "Industrials",
        "XLB": "Materials", "XLU": "Utilities",
        "XLRE": "Real Estate", "XLC": "Communications",
    }
    out = []
    for sym, name in sectors.items():
        try:
            d = yf.Ticker(sym).history(period="1mo", interval="1d")
            if d.empty or len(d) < 6:
                continue
            closes = d["Close"].values
            chg5 = (closes[-1] - closes[-6]) / closes[-6] * 100
            out.append({"symbol": sym, "name": name, "change_5d": round(float(chg5), 2)})
        except Exception:
            continue
    out.sort(key=lambda x: -x["change_5d"])
    result = {
        "leaders": out[:3],
        "laggards": out[-3:],
        "all": out,
    }
    _DF_CACHE[key] = (result, None, time.time())
    return result


def _fundamentals(sym: str) -> dict:
    """Pull free fundamental ratios from yfinance. Cached 1 hour per symbol.
    Returns P/E, P/B, ROE, debt/equity, dividend yield, short interest, FCF, etc."""
    key = (sym, "fundamentals")
    cached = _DF_CACHE.get(key)
    if cached and (time.time() - cached[2]) < 3600:
        return cached[0]
    try:
        info = yf.Ticker(sym).info or {}
        # Score fundamentals 0-100 (higher = stronger company)
        score = 50
        pe = info.get("trailingPE")
        fwd_pe = info.get("forwardPE")
        pb = info.get("priceToBook")
        peg = info.get("pegRatio")
        roe = info.get("returnOnEquity")
        d2e = info.get("debtToEquity")
        rev_g = info.get("revenueGrowth")
        eps_g = info.get("earningsGrowth")
        fcf = info.get("freeCashflow")
        div = info.get("dividendYield")
        short_pct = info.get("shortPercentOfFloat")
        rec_mean = info.get("recommendationMean")  # 1=strong buy, 5=strong sell

        # Heuristic scoring (each criterion ±5)
        if pe is not None:
            if 8 < pe < 25: score += 5
            elif pe > 50: score -= 8
        if fwd_pe is not None and pe is not None and fwd_pe < pe:
            score += 4   # earnings expected to grow
        if pb is not None:
            if pb < 3: score += 3
            elif pb > 10: score -= 5
        if peg is not None:
            if 0 < peg < 1.2: score += 6
            elif peg > 3: score -= 4
        if roe is not None:
            if roe > 0.15: score += 5
            elif roe < 0: score -= 6
        if d2e is not None:
            if d2e < 80: score += 3
            elif d2e > 200: score -= 5
        if rev_g is not None:
            if rev_g > 0.15: score += 5
            elif rev_g < 0: score -= 5
        if eps_g is not None:
            if eps_g > 0.15: score += 5
            elif eps_g < -0.10: score -= 6
        if fcf is not None and fcf > 0:
            score += 3
        if rec_mean is not None:
            if rec_mean < 2.0: score += 4
            elif rec_mean > 3.5: score -= 4

        score = max(0, min(100, score))
        if score >= 70:
            grade = "strong"
        elif score >= 55:
            grade = "good"
        elif score >= 40:
            grade = "neutral"
        else:
            grade = "weak"

        result = {
            "score": score, "grade": grade,
            "pe": pe, "forward_pe": fwd_pe, "pb": pb, "peg": peg,
            "roe": roe, "debt_equity": d2e,
            "revenue_growth": rev_g, "earnings_growth": eps_g,
            "free_cash_flow": fcf,
            "dividend_yield": div,
            "short_pct_float": short_pct,
            "analyst_rec_mean": rec_mean,
            "analyst_rec_label": (
                "strong buy" if rec_mean and rec_mean < 1.5
                else "buy" if rec_mean and rec_mean < 2.5
                else "hold" if rec_mean and rec_mean < 3.5
                else "sell" if rec_mean else "n/a"
            ),
        }
    except Exception as e:
        result = {"score": 50, "grade": "unknown", "_error": str(e)}
    _DF_CACHE[key] = (result, None, time.time())
    return result


def _short_squeeze_score(sym: str, ind: dict) -> dict:
    """Estimate short-squeeze risk: high short interest + low float + price compression
    near recent highs = squeeze fuel."""
    try:
        info = yf.Ticker(sym).info or {}
        short_pct = float(info.get("shortPercentOfFloat") or 0) * 100
        days_to_cover = float(info.get("shortRatio") or 0)
        bb_w = float(ind.get("bb_width_pct") or 5)
        rsi = float(ind.get("rsi14") or 50)
        # Score 0-100
        score = 0
        if short_pct > 20: score += 40
        elif short_pct > 10: score += 25
        elif short_pct > 5: score += 10
        if days_to_cover > 5: score += 25
        elif days_to_cover > 3: score += 15
        if bb_w < 4: score += 20  # compression
        if rsi > 60: score += 15  # already turning up
        return {"score": min(score, 100),
                "short_pct_float": short_pct,
                "days_to_cover": days_to_cover,
                "label": "high" if score >= 60 else "moderate" if score >= 35 else "low"}
    except Exception:
        return {"score": 0, "label": "unknown"}


def _market_regime() -> dict:
    """Classify the broad market into bull / bear / sideways / risk-off.
    Uses SPY 50/200 EMA cross + VIX level. Cached 5 min."""
    key = ("__MKT__", "regime")
    cached = _DF_CACHE.get(key)
    if cached and (time.time() - cached[2]) < 300:
        return cached[0]
    try:
        sdf = yf.Ticker("SPY").history(period="1y", interval="1d")
        vdf = yf.Ticker("^VIX").history(period="1mo", interval="1d")
        if sdf.empty or len(sdf) < 200:
            result = {"label": "unknown", "vix": 0.0, "spy_above_200ema": False}
        else:
            closes = sdf["Close"].values
            ema50 = pd.Series(closes).ewm(span=50).mean().values
            ema200 = pd.Series(closes).ewm(span=200).mean().values
            price = float(closes[-1])
            vix = float(vdf["Close"].values[-1]) if not vdf.empty else 18.0
            above200 = price > float(ema200[-1])
            golden = float(ema50[-1]) > float(ema200[-1])
            if vix > 28:
                label = "risk-off"
            elif golden and above200:
                label = "bull"
            elif (not golden) and (not above200):
                label = "bear"
            else:
                label = "sideways"
            result = {"label": label, "vix": round(vix, 1),
                      "spy_above_200ema": above200, "golden_cross": golden}
    except Exception:
        result = {"label": "unknown", "vix": 0.0, "spy_above_200ema": False}
    _DF_CACHE[key] = (result, None, time.time())
    return result


def run_agents_sync(sym: str, df: pd.DataFrame, info: dict, horizon: str = DEFAULT_HORIZON):
    ind = compute_all_indicators(df)
    ind["_symbol"] = sym
    ind["_horizon"] = horizon  # JudgeAgent reads this to scale stops/targets/threshold
    ind["_news"] = _LIVE_CACHE.get(sym, {}).get("news", [])
    # ── Higher-TF & market-context filters (accuracy boosters) ─────
    ind["weekly_trend"] = _weekly_trend(sym)
    # Per-horizon higher-timeframe trend (the #1 alpha source — the bigger
    # picture vetoes counter-trend trades on the current horizon)
    ind["_htf_trend"] = _horizon_htf_trend(sym, horizon)
    # Skip the SPY-vs-SPY tautology when analysing SPY itself
    ind["spy_trend"] = {"dir": "self", "pct_from_ema50": 0.0, "change_1d": 0.0} if sym == "SPY" else _spy_trend()
    ind["market_regime"] = _market_regime()
    ind["macro_basket"] = _macro_basket()
    ind["sector_rotation"] = _sector_rotation()
    ind["fundamentals"] = _fundamentals(sym)
    ind["short_squeeze"] = _short_squeeze_score(sym, ind)
    weights = LEARNING.get_weights()

    # Meta-learning multipliers (per regime + per symbol).
    regime_label = (ind.get("market_regime") or {}).get("label", "unknown")
    try:
        regime_mults = meta_learning.get_regime_multipliers()
        symbol_mults = meta_learning.get_symbol_multipliers()
    except Exception:
        regime_mults, symbol_mults = {}, {}

    # Evaluate AI-discovered strategies on the current indicator state.
    try:
        ind["discovered_strategies"] = meta_learning.evaluate_discovered(ind)
    except Exception:
        ind["discovered_strategies"] = {"fired": [], "lean": "HOLD",
                                        "score": 0.0, "confidence_boost": 0.0}

    votes = []
    for agent in AGENTS:
        try:
            vote = agent.analyze(df, ind)
        except Exception as e:
            vote = {"agent": agent.name, "emoji": "❓", "vote": "HOLD",
                    "confidence": 50.0, "reason": str(e)}
        w = weights.get(agent.name, 1.0)
        # Stack regime + symbol learning on top of base weight (each ±~30%)
        rw = regime_mults.get((agent.name, regime_label), 1.0)
        sw = symbol_mults.get((agent.name, sym), 1.0)
        eff_w = w * rw * sw
        vote["confidence"] = round(min(vote.get("confidence", 50) * eff_w, 97), 1)
        vote["weight"] = round(eff_w, 3)
        vote["weight_breakdown"] = {
            "base": round(w, 3),
            "regime": round(rw, 3),
            "symbol": round(sw, 3),
        }
        votes.append(vote)
    return votes, ind


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND TASK — live price loop
# ─────────────────────────────────────────────────────────────────────────────

async def live_price_loop():
    """Refresh live prices every 3 seconds and broadcast to WebSocket clients."""
    while True:
        for symbol in list(_WS_CLIENTS.keys()):
            if not _WS_CLIENTS.get(symbol):
                continue
            try:
                loop = asyncio.get_running_loop()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    data = await loop.run_in_executor(pool, _sync_fetch_live, symbol)
                _LIVE_CACHE[symbol] = data
                msg = json.dumps({"type": "live_price", **data})
                dead = set()
                for ws in list(_WS_CLIENTS[symbol]):
                    try:
                        await ws.send_text(msg)
                    except Exception:
                        dead.add(ws)
                _WS_CLIENTS[symbol] -= dead
            except Exception as e:
                logger.error(f"Live loop {symbol}: {e}")
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(live_price_loop())
    # Auto-refresh track_record.json + regime_stats.json on startup if stale,
    # then every 24h. Files are local-only (gitignored) so they survive git pulls.
    try:
        import auto_refresh
        auto_refresh.start_background_loop()
        logger.info("auto_refresh: background loop scheduled")
    except Exception as e:
        logger.warning(f"auto_refresh: failed to start ({e})")
    yield


app = FastAPI(title="TradeSignal AI v4", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# REST ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "agents": len(AGENTS) + 1, "version": "4.0-9agents"}


@app.get("/api/live/{symbol}")
def live_quote(symbol: str):
    sym = symbol.upper()
    if sym not in _LIVE_CACHE:
        _LIVE_CACHE[sym] = _sync_fetch_live(sym)
    return _LIVE_CACHE[sym]


@app.get("/api/news/{symbol}")
def get_news(symbol: str):
    sym = symbol.upper()
    cached = _LIVE_CACHE.get(sym, {})
    if not cached:
        data = _sync_fetch_live(sym)
        _LIVE_CACHE[sym] = data
        cached = data
    return {"symbol": sym, "news": cached.get("news", []), "ts": cached.get("ts", 0)}


@app.get("/api/fear-greed")
def fear_greed(nocache: bool = False):
    """Composite Fear & Greed Index (0-100) from VIX + P/C ratio + SPY momentum + junk bonds.
    Pass ?nocache=1 to bypass the 60-second cache (used by the manual refresh button)."""
    try:
        if nocache:
            FearGreedAgent._cache = {}
            FearGreedAgent._cache_ts = 0
        agent = FearGreedAgent()
        fg = agent._compute_fear_greed()
        return {"status": "ok", **_sanitize(fg)}
    except Exception as e:
        logger.error(f"Fear/Greed error: {e}")
        return {"error": str(e), "score": 50, "label": "Unknown"}


def _compute_stock_sentiment(sym: str, ind: dict | None = None) -> dict:
    """Per-stock sentiment 0-100 (Fear→Greed) built from technicals on THIS ticker.
    Unlike the market-wide F&G index, this score changes per symbol so the user
    sees something meaningful when switching stocks."""
    components = []
    try:
        if ind is None:
            df, _ = get_df(sym, period="3mo", interval="1d")
            ind = compute_all_indicators(df)

        def clamp(v, lo, hi):
            return max(lo, min(hi, v))

        # 1. RSI (30%) — a position-on-the-spectrum indicator
        rsi = float(ind.get("rsi14") or 50)
        rsi_score = clamp(rsi, 5, 95)
        components.append({"name": "RSI", "score": round(rsi_score, 1),
                           "value": round(rsi, 1), "weight": 0.30,
                           "label": f"RSI {rsi:.0f}"})

        # 2. Price vs VWAP (20%)
        p_vwap = float(ind.get("price_vs_vwap_pct") or 0)
        vwap_score = clamp(50 + p_vwap * 6, 5, 95)
        components.append({"name": "Price vs VWAP", "score": round(vwap_score, 1),
                           "value": round(p_vwap, 2), "weight": 0.20,
                           "label": f"{p_vwap:+.2f}% vs VWAP"})

        # 3. Trend score (20%)
        ts = float(ind.get("trend_score") or 0)
        ts_score = clamp(50 + ts * 8, 5, 95)
        components.append({"name": "Trend", "score": round(ts_score, 1),
                           "value": round(ts, 2), "weight": 0.20,
                           "label": f"Trend {ts:+.1f}"})

        # 4. Volume confirmation (15%) — high volume on up day = greed; on down day = fear
        rv = float(ind.get("rel_volume") or 1)
        chg = float(ind.get("change_1d") or 0)
        delta = clamp((rv - 1) * 30, -30, 30)
        vol_score = clamp(50 + (delta if chg >= 0 else -delta), 5, 95)
        components.append({"name": "Volume", "score": round(vol_score, 1),
                           "value": round(rv, 2), "weight": 0.15,
                           "label": f"Rel vol {rv:.1f}x"})

        # 5. MACD histogram (15%)
        mh = float(ind.get("macd_hist") or 0)
        macd_score = clamp(50 + mh * 200, 5, 95)
        components.append({"name": "MACD", "score": round(macd_score, 1),
                           "value": round(mh, 3), "weight": 0.15,
                           "label": "MACD bullish" if mh >= 0 else "MACD bearish"})

        total_w = sum(c["weight"] for c in components)
        score = round(sum(c["score"] * c["weight"] for c in components) / total_w, 1) if total_w else 50.0

        if score >= 75: label, color = "Extreme Greed", "#10b981"
        elif score >= 55: label, color = "Greed", "#22c55e"
        elif score >= 45: label, color = "Neutral", "#f59e0b"
        elif score >= 25: label, color = "Fear", "#f97316"
        else: label, color = "Extreme Fear", "#ef4444"

        return {"symbol": sym, "score": score, "label": label,
                "color": color, "components": components}
    except Exception as e:
        logger.error(f"Stock sentiment {sym}: {e}")
        return {"symbol": sym, "score": 50, "label": "Unknown",
                "color": "#94a3b8", "components": [], "error": str(e)}


@app.get("/api/stock-sentiment/{symbol}")
def stock_sentiment(symbol: str):
    """Per-symbol Fear/Greed-style score derived from this stock's own technicals."""
    return _sanitize(_compute_stock_sentiment(symbol.upper()))


@app.get("/api/search")
def search_symbols(q: str = "", limit: int = 8):
    """Suggest tickers matching a query (e.g. 'app' → AAPL, APP, APPS).
    Used by the dashboard's autocomplete dropdown so users don't have to know exact tickers."""
    q = (q or "").strip()
    if len(q) < 1:
        return {"query": q, "results": []}
    try:
        url = (
            "https://query1.finance.yahoo.com/v1/finance/search"
            f"?q={urllib.parse.quote(q)}&quotesCount={int(limit)}&newsCount=0&listsCount=0"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())
        results = []
        for item in (data.get("quotes") or [])[: int(limit)]:
            sym = item.get("symbol") or ""
            if not sym:
                continue
            results.append({
                "symbol": sym,
                "name": item.get("shortname") or item.get("longname") or "",
                "exchange": item.get("exchDisp") or item.get("exchange") or "",
                "type": item.get("quoteType") or item.get("typeDisp") or "",
            })
        return {"query": q, "results": results}
    except Exception as e:
        logger.error(f"Search error '{q}': {e}")
        return {"query": q, "results": [], "error": str(e)}


@app.get("/api/political-news")
def political_news():
    """Live political/macro news from Google News RSS (Trump, tariff, Fed, economy)."""
    try:
        agent = PoliticalAgent()
        news = agent._fetch_political_news()
        return {"status": "ok", "count": len(news), "news": news}
    except Exception as e:
        logger.error(f"Political news error: {e}")
        return {"error": str(e), "news": []}


@app.get("/api/options-chain/{symbol}")
def options_chain(symbol: str, expiry: str = ""):
    """Live options chain — calls & puts near the money, with IV, OI, bid/ask."""
    sym = symbol.upper()
    try:
        t = yf.Ticker(sym)
        dates = t.options
        if not dates:
            return {"error": "No options data available", "symbol": sym}

        # Pick expiry — default to first available, or the requested one
        selected = expiry if expiry in dates else dates[0]
        chain = t.option_chain(selected)

        # Current price for ITM detection and filtering
        price = 0.0
        try:
            price = safe_float(getattr(t.fast_info, "last_price", None) or
                               getattr(t.fast_info, "regularMarketPrice", None))
        except Exception:
            pass

        def clean_chain(df, side: str):
            rows = []
            for _, r in df.iterrows():
                strike = safe_float(r.get("strike", 0))
                if price > 0 and abs(strike - price) / price > 0.15:
                    continue  # only show ±15% from current price
                last  = safe_float(r.get("lastPrice", 0))
                bid   = safe_float(r.get("bid", 0))
                ask   = safe_float(r.get("ask", 0))
                mid   = round((bid + ask) / 2, 2) if bid and ask else last
                vol   = int(r.get("volume", 0) or 0)
                oi    = int(r.get("openInterest", 0) or 0)
                iv    = round(safe_float(r.get("impliedVolatility", 0)) * 100, 1)
                itm   = bool(r.get("inTheMoney", False))
                delta_est = 0.0
                if price > 0 and side == "call":
                    delta_est = round(min(0.99, max(0.01, (price - strike * 0.995) / (price * 0.15 + 1))), 2)
                elif price > 0 and side == "put":
                    delta_est = round(-min(0.99, max(0.01, (strike * 1.005 - price) / (price * 0.15 + 1))), 2)
                rows.append({
                    "strike": strike, "last": last, "bid": bid, "ask": ask,
                    "mid": mid, "volume": vol, "open_interest": oi,
                    "iv": iv, "itm": itm, "delta": delta_est,
                })
            rows.sort(key=lambda x: x["strike"])
            return rows

        calls = clean_chain(chain.calls, "call")
        puts  = clean_chain(chain.puts, "put")

        return {
            "symbol": sym,
            "price": round(price, 2),
            "selected_expiry": selected,
            "available_expiries": list(dates[:8]),
            "calls": calls,
            "puts": puts,
            "calls_count": len(calls),
            "puts_count": len(puts),
        }
    except Exception as e:
        logger.error(f"Options chain error {sym}: {e}")
        return {"error": str(e), "symbol": sym}


@app.get("/api/accuracy")
def accuracy_report():
    """Full accuracy report across all agents and signals with method descriptions."""
    try:
        report = LEARNING.get_accuracy_report()
        agent_methods = {a.name: getattr(a, "method", "") for a in AGENTS}
        agent_methods[JUDGE.name] = "6/9 consensus threshold — fires CALL/PUT only on strong agreement"
        return {
            "status": "ok",
            "report": report,
            "agent_methods": agent_methods,
        }
    except Exception as e:
        logger.error(f"Accuracy report error: {e}")
        return {"error": str(e)}


@app.get("/api/accuracy/{symbol}")
def accuracy_by_symbol(symbol: str):
    """Per-symbol prediction history and outcomes."""
    sym = symbol.upper()
    try:
        history = LEARNING.get_history(sym)
        return {"symbol": sym, "status": "ok", **history}
    except Exception as e:
        return {"error": str(e), "symbol": sym}


@app.get("/api/horizons")
def list_horizons():
    """Return the available prediction horizons (for the UI dropdown)."""
    return {
        "default": DEFAULT_HORIZON,
        "horizons": [
            {
                "key": k,
                "label": v["label"],
                "interval": v["interval"],
                "period": v["period"],
                "forecast_bars": v["forecast_bars"],
                "bar_minutes": v["bar_minutes"],
                "threshold": v["threshold"],
                "expiry_pref": v.get("expiry_pref", ""),
            }
            for k, v in HORIZONS.items()
        ],
    }


@app.get("/api/chart/{symbol}")
def chart_data(symbol: str, period: str = "3mo", interval: str = "1d"):
    sym = symbol.upper()
    if interval == "auto":
        auto_map = {"1d": "5m", "5d": "15m", "1mo": "1d", "3mo": "1d", "6mo": "1d"}
        interval = auto_map.get(period, "1d")

    try:
        df, _ = get_df(sym, period=period, interval=interval)
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df = df.dropna(subset=["Close"])

        closes = df["Close"].values.astype(float)
        opens = df["Open"].values.astype(float)
        highs = df["High"].values.astype(float)
        lows = df["Low"].values.astype(float)
        vols = df["Volume"].fillna(0).values.astype(float)
        n = len(closes)

        from indicators import (compute_rsi, compute_macd, compute_bollinger,
                                compute_vwap, compute_supertrend)

        ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().values
        ema21 = pd.Series(closes).ewm(span=21, adjust=False).mean().values
        ema50 = pd.Series(closes).ewm(span=50, adjust=False).mean().values
        rsi_vals = compute_rsi(closes)
        macd_line, macd_sig, macd_hist = compute_macd(closes)
        bb_u, bb_m, bb_l, _ = compute_bollinger(closes)
        vwap_vals = compute_vwap(highs, lows, closes, vols)
        st_vals, st_dir = compute_supertrend(highs, lows, closes)

        raw_ts = [int(t.timestamp()) for t in df.index]
        timestamps = []
        last_ts = -1
        for ts in raw_ts:
            if ts <= last_ts:
                ts = last_ts + 1
            timestamps.append(ts)
            last_ts = ts

        def safe(v):
            if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                return None
            return round(float(v), 4)

        candles, emas, bbands, rsi_out, macd_out, vol_out, vwap_out, st_out = (
            [], [], [], [], [], [], [], []
        )

        for i in range(n):
            ts = timestamps[i]
            if np.isnan(closes[i]):
                continue
            candles.append({"time": ts, "open": safe(opens[i]),
                             "high": safe(highs[i]), "low": safe(lows[i]),
                             "close": safe(closes[i])})
            emas.append({"time": ts, "ema9": safe(ema9[i]),
                          "ema21": safe(ema21[i]), "ema50": safe(ema50[i])})
            bbands.append({"time": ts, "upper": safe(bb_u[i]),
                            "mid": safe(bb_m[i]), "lower": safe(bb_l[i])})
            rsi_out.append({"time": ts, "value": safe(rsi_vals[i])})
            macd_out.append({"time": ts, "macd": safe(macd_line[i]),
                              "signal": safe(macd_sig[i]), "hist": safe(macd_hist[i])})
            is_green = i == 0 or closes[i] >= closes[i-1]
            vol_out.append({"time": ts, "value": float(vols[i]),
                             "color": "#26a69a" if is_green else "#ef5350"})
            vwap_out.append({"time": ts, "value": safe(vwap_vals[i])})
            st_out.append({"time": ts, "value": safe(st_vals[i]),
                            "direction": "up" if st_dir[i] > 0 else "down"})

        return {
            "symbol": sym, "period": period, "interval": interval,
            "candles": candles, "ema": emas, "bb": bbands,
            "rsi": rsi_out, "macd": macd_out, "volume": vol_out,
            "vwap": vwap_out, "supertrend": st_out,
        }
    except Exception as e:
        logger.error(f"Chart error {sym}: {e}")
        return {"error": str(e), "symbol": sym}


@app.get("/api/analyze/{symbol}")
def analyze(symbol: str, period: str = "", horizon: str = DEFAULT_HORIZON):
    sym = symbol.upper()
    try:
        # Horizon picks the right data window unless the caller overrides period
        h = get_horizon_config(horizon)
        use_period = period or h["period"]
        use_interval = h["interval"]
        df, info = get_df(sym, period=use_period, interval=use_interval)
        votes, ind = run_agents_sync(sym, df, info, horizon=h["key"])
        judgment = JUDGE.decide(votes, ind)
        pred_id = LEARNING.save_prediction(
            symbol=sym, signal=judgment["signal"],
            confidence=judgment["confidence"],
            entry_price=judgment["entry_price"],
            target_price=judgment["target_price"],
            stop_loss=judgment["stop_loss"],
            agent_votes={v["agent"]: v["vote"] for v in votes},
        )
        try:
            meta_learning.save_snapshot(pred_id, sym, judgment["signal"], ind)
        except Exception:
            pass
        safe_ind = _sanitize(ind)
        return {
            "symbol": sym,
            "agents": votes,
            "judgment": judgment,
            "indicators": safe_ind,
            "forecast_line": judgment.get("forecast_line", []),
        }
    except Exception as e:
        logger.error(f"Analyze error {sym}: {e}")
        return {"error": str(e), "symbol": sym}


@app.get("/api/quick/{symbol}")
def quick_signal(symbol: str):
    sym = symbol.upper()
    try:
        df, info = get_df(sym, period="1mo")
        votes, ind = run_agents_sync(sym, df, info)
        judgment = JUDGE.decide(votes, ind)
        return {
            "symbol": sym,
            "signal": judgment["signal"],
            "confidence": judgment["confidence"],
            "price": ind.get("price", 0),
            "change_pct": ind.get("change_1d", 0),
            "call_votes": judgment["vote_tally"]["BUY_CALL"],
            "put_votes": judgment["vote_tally"]["BUY_PUT"],
            "hold_votes": judgment["vote_tally"]["HOLD"],
        }
    except Exception as e:
        return {"error": str(e), "symbol": sym}


@app.get("/api/watchlist")
def watchlist(symbols: str = "AAPL,MSFT,NVDA,TSLA,SPY,QQQ"):
    results = []
    for sym in symbols.upper().split(","):
        sym = sym.strip()
        if sym:
            results.append(quick_signal(sym))
    return results


@app.get("/api/learning/weights")
def learning_weights():
    return LEARNING.get_accuracy_report()


@app.get("/api/learning/history/{symbol}")
def learning_history(symbol: str):
    return LEARNING.get_history(symbol.upper())


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN — auto-refresh status + manual triggers for the data files
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/admin/refresh/status")
def admin_refresh_status():
    """How fresh are track_record.json + regime_stats.json? When were they last regenerated?"""
    import auto_refresh
    return auto_refresh.get_status()


@app.post("/api/admin/refresh/track-record")
async def admin_refresh_track_record(force: bool = False):
    """Trigger a track-record regeneration NOW. Blocks ~1-2 minutes; returns the result."""
    import auto_refresh
    return await auto_refresh.refresh_track_record(force=force)


@app.post("/api/admin/refresh/regime-stats")
async def admin_refresh_regime_stats(force: bool = False):
    """Trigger a regime-stats regeneration NOW. Blocks ~1-2 minutes; returns the result."""
    import auto_refresh
    return await auto_refresh.refresh_regime_stats(force=force)


@app.post("/api/admin/learn/discover")
def admin_discover_strategies():
    """
    Mine the indicator-snapshot log for self-discovered indicator combinations
    that have an empirical edge. Also refreshes per-regime / per-symbol agent
    accuracy. Returns the list of discovered strategies.
    """
    try:
        meta_learning.update_regime_symbol_perf()
        rules = meta_learning.discover_strategies()
        return {"ok": True, "count": len(rules), "strategies": rules}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/admin/learn/status")
def admin_learn_status():
    """Inspect what the meta-learning layer currently knows."""
    try:
        rules = meta_learning.load_strategies()
        rmults = meta_learning.get_regime_multipliers()
        smults = meta_learning.get_symbol_multipliers()
        return {
            "discovered_strategies": rules,
            "regime_multipliers": [
                {"agent": k[0], "regime": k[1], "multiplier": v}
                for k, v in rmults.items()
            ],
            "symbol_multipliers": [
                {"agent": k[0], "symbol": k[1], "multiplier": v}
                for k, v in smults.items()
            ],
        }
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET — 9-agent live analysis + live price streaming every 3s
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/api/ws/analyze/{symbol}")
async def ws_analyze(websocket: WebSocket, symbol: str):
    await websocket.accept()
    sym = symbol.upper()
    # Horizon comes from the connection query string: /api/ws/analyze/AAPL?horizon=swing
    horizon_q = (websocket.query_params.get("horizon") or DEFAULT_HORIZON).lower()
    h_cfg = get_horizon_config(horizon_q)

    if sym not in _WS_CLIENTS:
        _WS_CLIENTS[sym] = set()
    _WS_CLIENTS[sym].add(websocket)

    try:
        loop = asyncio.get_running_loop()

        # 1. Live price immediately
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            live = await loop.run_in_executor(pool, _sync_fetch_live, sym)
        _LIVE_CACHE[sym] = live
        await websocket.send_text(json.dumps({"type": "live_price", **live}))
        await websocket.send_text(json.dumps({
            "type": "status", "message": f"🔍 Fetching {sym} market data..."
        }))

        # 2. Candle data — period/interval driven by the chosen horizon
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            df, info = await loop.run_in_executor(
                pool, get_df, sym, h_cfg["period"], h_cfg["interval"]
            )

        await websocket.send_text(json.dumps({
            "type": "status",
            "message": f"📊 {sym} loaded ({h_cfg['label']}) — running 9 specialist agents..."
        }))

        # 3. Build indicators once
        ind = compute_all_indicators(df)
        ind["_symbol"] = sym
        ind["_horizon"] = h_cfg["key"]
        ind["_news"] = live.get("news", [])
        # Per-horizon higher-timeframe trend gate
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            ind["_htf_trend"] = await loop.run_in_executor(
                pool, _horizon_htf_trend, sym, h_cfg["key"]
            )
        weights = LEARNING.get_weights()

        await websocket.send_text(json.dumps({
            "type": "status",
            "message": f"🤖 All 9 agents running in parallel..."
        }))

        # 4. Run all agents in parallel, then send all votes + judgment in one message
        def run_agent(agent):
            vote = agent.analyze(df, ind)
            w = weights.get(agent.name, 1.0)
            vote["confidence"] = round(min(vote.get("confidence", 50) * w, 97), 1)
            vote["weight"] = round(w, 3)
            vote["method"] = getattr(agent, "method", "")
            return vote

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(AGENTS)) as pool:
            futs = [pool.submit(run_agent, agent) for agent in AGENTS]
            votes = [f.result() for f in futs]

        # 5. Judge
        judgment = JUDGE.decide(votes, ind)

        # 6. Persist
        pred_id = LEARNING.save_prediction(
            symbol=sym, signal=judgment["signal"],
            confidence=judgment["confidence"],
            entry_price=judgment["entry_price"],
            target_price=judgment["target_price"],
            stop_loss=judgment["stop_loss"],
            agent_votes={v["agent"]: v["vote"] for v in votes},
        )
        try:
            meta_learning.save_snapshot(pred_id, sym, judgment["signal"], ind)
        except Exception:
            pass

        # 7. Verify past outcomes
        if live.get("price", 0):
            try:
                LEARNING.verify_outcomes(sym, live["price"])
            except Exception:
                pass

        # 8. Accuracy snapshot
        accuracy = {}
        try:
            accuracy = LEARNING.get_accuracy_report()
        except Exception:
            pass

        safe_ind = _sanitize(ind)

        # Per-stock sentiment (changes per ticker so the gauge isn't frozen)
        stock_sent = _sanitize(_compute_stock_sentiment(sym, ind))

        await websocket.send_text(json.dumps({
            "type": "judgment",
            "judgment": judgment,
            "votes": votes,
            "indicators": safe_ind,
            "forecast_line": judgment.get("forecast_line", []),
            "accuracy": accuracy,
            "stock_sentiment": stock_sent,
        }))

        # 9. Keep alive
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                if msg == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                pass
            except Exception:
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass
    finally:
        _WS_CLIENTS.get(sym, set()).discard(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
