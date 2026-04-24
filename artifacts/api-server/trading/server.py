"""
Trading Prediction System — FastAPI Server
8 AI Agents | Yahoo Finance | Learning System | Live Voting WebSocket
"""
import os
import asyncio
import logging
import json
from datetime import datetime, timezone
from typing import Optional
import numpy as np

import yfinance as yf
import pandas as pd
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from agents import (
    PriceActionAgent, TechnicalAgent, VolumeAgent, SentimentAgent,
    OptionsFlowAgent, MomentumAgent, RiskAgent, JudgeAgent,
)
from learning import (
    init_db, save_prediction, check_and_update_outcomes,
    get_agent_weights, get_recent_predictions, get_accuracy_stats,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize learning DB
init_db()

app = FastAPI(title="TradeSignal AI", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Agents
analyst_agents = [
    PriceActionAgent(), TechnicalAgent(), VolumeAgent(), SentimentAgent(),
    OptionsFlowAgent(), MomentumAgent(), RiskAgent(),
]
judge = JudgeAgent()


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)


manager = ConnectionManager()


def safe_float(val, default=0.0):
    try:
        f = float(val)
        return f if not np.isnan(f) and not np.isinf(f) else default
    except Exception:
        return default


def fetch_market_data(symbol: str, period: str = "3mo"):
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period)
    if df.empty:
        raise ValueError(f"No data for symbol: {symbol}")
    info = {}
    try:
        info = ticker.info or {}
    except Exception:
        pass
    info["symbol"] = symbol
    try:
        info["news"] = ticker.news or []
    except Exception:
        info["news"] = []
    return df, info


def apply_agent_weights(votes: list, weights: dict) -> list:
    """Apply learned weights to agent confidence scores."""
    weighted = []
    for v in votes:
        agent_name = v.get("agent", "")
        w = weights.get(agent_name, {}).get("weight", 1.0)
        adjusted = v.copy()
        adjusted["confidence"] = round(min(v.get("confidence", 50) * w, 98), 1)
        adjusted["weight"] = round(w, 3)
        adjusted["accuracy"] = weights.get(agent_name, {}).get("accuracy", 0.5)
        adjusted["predictions_tracked"] = weights.get(agent_name, {}).get("total", 0)
        weighted.append(adjusted)
    return weighted


def run_all_agents(symbol: str, period: str = "3mo") -> dict:
    df, info = fetch_market_data(symbol, period)
    price = safe_float(df["Close"].iloc[-1])
    timestamp = datetime.now(timezone.utc).isoformat()

    # Get learned weights
    weights = get_agent_weights()

    # Check old predictions for this symbol
    try:
        check_and_update_outcomes(symbol, price)
    except Exception as e:
        logger.warning(f"Outcome check failed: {e}")

    votes = []
    for agent in analyst_agents:
        try:
            result = agent.analyze(df, info)
            result["timestamp"] = timestamp
        except Exception as e:
            result = {
                "agent": agent.name, "emoji": getattr(agent, "emoji", "🤖"),
                "vote": "HOLD", "confidence": 50.0,
                "reason": f"Agent error: {str(e)}", "timestamp": timestamp,
            }
        votes.append(result)

    votes = apply_agent_weights(votes, weights)

    risk_data = next((v for v in votes if v["agent"] == "Risk Agent"), {})
    judgment = judge.decide(votes, price, risk_data)

    # Save prediction for learning
    try:
        pred_id = save_prediction(symbol, judgment, votes)
        judgment["prediction_id"] = pred_id
    except Exception as e:
        logger.warning(f"Failed to save prediction: {e}")
        judgment["prediction_id"] = None

    ticker_data = {
        "symbol": symbol.upper(),
        "price": round(price, 2),
        "prev_close": round(safe_float(df["Close"].iloc[-2]) if len(df) >= 2 else price, 2),
        "change_pct": round(
            (price - safe_float(df["Close"].iloc[-2])) / safe_float(df["Close"].iloc[-2]) * 100
            if len(df) >= 2 else 0, 2),
        "volume": int(safe_float(df["Volume"].iloc[-1])),
        "avg_volume": int(np.mean(df["Volume"].values[-20:])),
        "market_cap": safe_float(info.get("marketCap", 0)),
        "company_name": info.get("longName", symbol.upper()),
        "sector": info.get("sector", "N/A"),
        "pe_ratio": round(safe_float(info.get("trailingPE", 0)), 2),
        "week_52_high": round(safe_float(info.get("fiftyTwoWeekHigh", 0)), 2),
        "week_52_low": round(safe_float(info.get("fiftyTwoWeekLow", 0)), 2),
        "timestamp": timestamp,
    }

    return {"ticker": ticker_data, "agent_votes": votes, "judgment": judgment}


# ---------------------------------------------------------------------------
# Chart data endpoint
# ---------------------------------------------------------------------------
@app.get("/api/chart/{symbol}")
def chart_data(symbol: str, period: str = "3mo"):
    """Return OHLCV + technical indicator data for charting."""
    try:
        symbol = symbol.upper().strip()
        df, info = fetch_market_data(symbol, period)
        closes = df["Close"]
        highs = df["High"]
        lows = df["Low"]
        opens = df["Open"]
        volumes = df["Volume"]

        # EMA lines
        ema9 = closes.ewm(span=9).mean()
        ema21 = closes.ewm(span=21).mean()
        ema50 = closes.ewm(span=50).mean()

        # Bollinger Bands
        ma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        bb_upper = ma20 + 2 * std20
        bb_lower = ma20 - 2 * std20

        # RSI
        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        # MACD
        ema12 = closes.ewm(span=12).mean()
        ema26 = closes.ewm(span=26).mean()
        macd_line = ema12 - ema26
        macd_signal = macd_line.ewm(span=9).mean()
        macd_hist = macd_line - macd_signal

        def ts(idx):
            try:
                return int(idx.timestamp())
            except Exception:
                return 0

        candles = []
        for i in range(len(df)):
            idx = df.index[i]
            candles.append({
                "time": ts(idx),
                "open": round(safe_float(opens.iloc[i]), 4),
                "high": round(safe_float(highs.iloc[i]), 4),
                "low": round(safe_float(lows.iloc[i]), 4),
                "close": round(safe_float(closes.iloc[i]), 4),
                "volume": int(safe_float(volumes.iloc[i])),
            })

        def series(data):
            out = []
            for i in range(len(df)):
                v = safe_float(data.iloc[i], None)
                if v is not None and not np.isnan(v):
                    out.append({"time": ts(df.index[i]), "value": round(v, 4)})
            return out

        return {
            "symbol": symbol,
            "candles": candles,
            "indicators": {
                "ema9": series(ema9),
                "ema21": series(ema21),
                "ema50": series(ema50),
                "bb_upper": series(bb_upper),
                "bb_lower": series(bb_lower),
                "bb_mid": series(ma20),
                "rsi": series(rsi),
                "macd_line": series(macd_line),
                "macd_signal": series(macd_signal),
                "macd_hist": series(macd_hist),
                "volume": [{"time": ts(df.index[i]), "value": int(safe_float(volumes.iloc[i]))}
                           for i in range(len(df))],
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Chart data failed: {symbol}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return {"status": "ok", "service": "TradeSignal AI", "version": "2.0.0", "agents": 8}


@app.get("/api/health")
def health():
    return {"status": "healthy", "agents": 8, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/analyze/{symbol}")
def analyze(symbol: str, period: str = "3mo"):
    try:
        result = run_all_agents(symbol.upper().strip(), period)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Analysis failed: {symbol}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/quick/{symbol}")
def quick_signal(symbol: str):
    try:
        result = run_all_agents(symbol.upper().strip())
        j = result["judgment"]
        t = result["ticker"]
        return {
            "symbol": t["symbol"], "price": t["price"],
            "signal": j["signal"], "confidence": j["confidence"],
            "vote_tally": j["vote_tally"], "entry_price": j["entry_price"],
            "stop_loss": j["stop_loss"], "target_price": j["target_price"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/watchlist")
def watchlist(symbols: str = "AAPL,MSFT,NVDA,TSLA,SPY"):
    results = []
    for sym in symbols.split(","):
        sym = sym.strip().upper()
        if not sym:
            continue
        try:
            result = run_all_agents(sym)
            j = result["judgment"]
            t = result["ticker"]
            results.append({
                "symbol": sym, "price": t["price"],
                "change_pct": t["change_pct"], "signal": j["signal"],
                "confidence": j["confidence"], "vote_tally": j["vote_tally"],
            })
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})
    return {"watchlist": results, "count": len(results)}


@app.get("/api/learning/weights")
def agent_weights():
    """Return learned agent weights and accuracy stats."""
    weights = get_agent_weights()
    stats = get_accuracy_stats()
    return {"weights": weights, "overall_stats": stats}


@app.get("/api/learning/history/{symbol}")
def prediction_history(symbol: str, limit: int = 20):
    preds = get_recent_predictions(symbol.upper(), limit)
    return {"symbol": symbol.upper(), "predictions": preds, "count": len(preds)}


# ---------------------------------------------------------------------------
# WebSocket — live streaming analysis
# ---------------------------------------------------------------------------
@app.websocket("/api/ws/analyze/{symbol}")
async def ws_analyze(websocket: WebSocket, symbol: str):
    await manager.connect(websocket)
    try:
        symbol = symbol.upper().strip()
        await websocket.send_json({"type": "start", "symbol": symbol})

        try:
            df, info = fetch_market_data(symbol)
            price = safe_float(df["Close"].iloc[-1])
        except Exception as e:
            await websocket.send_json({"type": "error", "message": str(e)})
            return

        ticker_data = {
            "symbol": symbol,
            "price": round(price, 2),
            "prev_close": round(safe_float(df["Close"].iloc[-2]) if len(df) >= 2 else price, 2),
            "change_pct": round(
                (price - safe_float(df["Close"].iloc[-2])) / safe_float(df["Close"].iloc[-2]) * 100
                if len(df) >= 2 else 0, 2),
            "volume": int(safe_float(df["Volume"].iloc[-1])),
            "avg_volume": int(np.mean(df["Volume"].values[-20:])),
            "company_name": info.get("longName", symbol),
            "sector": info.get("sector", "N/A"),
            "week_52_high": round(safe_float(info.get("fiftyTwoWeekHigh", 0)), 2),
            "week_52_low": round(safe_float(info.get("fiftyTwoWeekLow", 0)), 2),
        }
        await websocket.send_json({"type": "ticker", "data": ticker_data})

        # Check old outcomes
        try:
            check_and_update_outcomes(symbol, price)
        except Exception:
            pass

        weights = get_agent_weights()
        votes = []
        timestamp = datetime.now(timezone.utc).isoformat()

        for agent in analyst_agents:
            await asyncio.sleep(0.5)
            try:
                result = agent.analyze(df, info)
                result["timestamp"] = timestamp
            except Exception as e:
                result = {
                    "agent": agent.name, "emoji": getattr(agent, "emoji", "🤖"),
                    "vote": "HOLD", "confidence": 50.0,
                    "reason": f"Error: {str(e)}", "timestamp": timestamp,
                }
            # Apply weight
            w = weights.get(result["agent"], {}).get("weight", 1.0)
            result["confidence"] = round(min(result.get("confidence", 50) * w, 98), 1)
            result["weight"] = round(w, 3)
            result["accuracy"] = weights.get(result["agent"], {}).get("accuracy", 0.5)
            result["predictions_tracked"] = weights.get(result["agent"], {}).get("total", 0)
            votes.append(result)
            await websocket.send_json({"type": "agent_vote", "data": result})

        await asyncio.sleep(0.7)
        risk_data = next((v for v in votes if v["agent"] == "Risk Agent"), {})
        judgment = judge.decide(votes, price, risk_data)

        try:
            pred_id = save_prediction(symbol, judgment, votes)
            judgment["prediction_id"] = pred_id
        except Exception:
            judgment["prediction_id"] = None

        await websocket.send_json({"type": "judgment", "data": judgment})
        await websocket.send_json({"type": "complete"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
