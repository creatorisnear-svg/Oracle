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


def get_df(symbol: str, period: str = "3mo", interval: str = "1d"):
    t = yf.Ticker(symbol)
    df = t.history(period=period, interval=interval)
    if df.empty:
        raise ValueError(f"No data for {symbol}")
    info = {}
    try:
        info = t.info
    except Exception:
        pass
    return df, info


def run_agents_sync(sym: str, df: pd.DataFrame, info: dict):
    ind = compute_all_indicators(df)
    ind["_symbol"] = sym
    ind["_news"] = _LIVE_CACHE.get(sym, {}).get("news", [])
    weights = LEARNING.get_weights()
    votes = []
    for agent in AGENTS:
        try:
            vote = agent.analyze(df, ind)
        except Exception as e:
            vote = {"agent": agent.name, "emoji": "❓", "vote": "HOLD",
                    "confidence": 50.0, "reason": str(e)}
        w = weights.get(agent.name, 1.0)
        vote["confidence"] = round(min(vote.get("confidence", 50) * w, 97), 1)
        vote["weight"] = round(w, 3)
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
def fear_greed():
    """Composite Fear & Greed Index (0-100) from VIX + P/C ratio + SPY momentum + junk bonds."""
    try:
        agent = FearGreedAgent()
        fg = agent._compute_fear_greed()
        return {"status": "ok", **_sanitize(fg)}
    except Exception as e:
        logger.error(f"Fear/Greed error: {e}")
        return {"error": str(e), "score": 50, "label": "Unknown"}


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
def analyze(symbol: str, period: str = "3mo"):
    sym = symbol.upper()
    try:
        df, info = get_df(sym, period=period)
        votes, ind = run_agents_sync(sym, df, info)
        judgment = JUDGE.decide(votes, ind)
        LEARNING.save_prediction(
            symbol=sym, signal=judgment["signal"],
            confidence=judgment["confidence"],
            entry_price=judgment["entry_price"],
            target_price=judgment["target_price"],
            stop_loss=judgment["stop_loss"],
            agent_votes={v["agent"]: v["vote"] for v in votes},
        )
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
# WEBSOCKET — 9-agent live analysis + live price streaming every 3s
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/api/ws/analyze/{symbol}")
async def ws_analyze(websocket: WebSocket, symbol: str):
    await websocket.accept()
    sym = symbol.upper()

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

        # 2. Candle data
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            df, info = await loop.run_in_executor(pool, get_df, sym, "3mo", "1d")

        await websocket.send_text(json.dumps({
            "type": "status",
            "message": f"📊 {sym} loaded — running 9 specialist agents..."
        }))

        # 3. Build indicators once
        ind = compute_all_indicators(df)
        ind["_symbol"] = sym
        ind["_news"] = live.get("news", [])
        weights = LEARNING.get_weights()

        votes = []

        # 4. Stream each agent vote
        for agent in AGENTS:
            await websocket.send_text(json.dumps({
                "type": "status",
                "message": f"{agent.emoji} {agent.name} analyzing..."
            }))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                vote = await loop.run_in_executor(pool, agent.analyze, df, ind)
            w = weights.get(agent.name, 1.0)
            vote["confidence"] = round(min(vote.get("confidence", 50) * w, 97), 1)
            vote["weight"] = round(w, 3)
            vote["method"] = getattr(agent, "method", "")
            votes.append(vote)
            await websocket.send_text(json.dumps({"type": "agent_vote", "vote": vote}))
            await asyncio.sleep(0.15)

        # 5. Judge
        judgment = JUDGE.decide(votes, ind)

        # 6. Persist
        LEARNING.save_prediction(
            symbol=sym, signal=judgment["signal"],
            confidence=judgment["confidence"],
            entry_price=judgment["entry_price"],
            target_price=judgment["target_price"],
            stop_loss=judgment["stop_loss"],
            agent_votes={v["agent"]: v["vote"] for v in votes},
        )

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

        await websocket.send_text(json.dumps({
            "type": "judgment",
            "judgment": judgment,
            "indicators": safe_ind,
            "forecast_line": judgment.get("forecast_line", []),
            "accuracy": accuracy,
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
