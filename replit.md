# TradeSignal AI — 8-Agent Trading Prediction System v2

## Overview
Professional trading prediction system with 8 specialized AI agents, interactive TradingView-quality charts, a learning system that tracks prediction accuracy over time, and a live WebSocket streaming dashboard.

## Architecture
- **Backend**: Python FastAPI (`artifacts/api-server/trading/`)
- **Frontend**: React + Vite + Tailwind dashboard (`artifacts/mockup-sandbox/src/`)
- **Charts**: TradingView Lightweight Charts v5 (candlestick, EMA, Bollinger Bands, RSI, MACD, Volume)
- **Data**: Yahoo Finance via `yfinance`
- **Learning**: SQLite database tracking predictions & adjusting agent weights over time
- **Real-time**: WebSocket streaming — agents vote one-by-one on the live dashboard

## The 8 Agents (`trading/agents.py`)

| # | Agent | Emoji | Signal Logic |
|---|-------|-------|--------------|
| 1 | Price Action Agent | 📊 | Engulfing candles, hammers, shooting stars, trend structure |
| 2 | Technical Agent | 📈 | RSI, MACD crossover, Bollinger Bands, EMA 9/21 crossover |
| 3 | Volume Agent | 📦 | Volume spike (vs 20-day avg), OBV trend, Money Flow Index |
| 4 | Sentiment Agent | 📰 | News headline scoring (bullish/bearish keywords), 5-day price proxy |
| 5 | Options Flow Agent | 🎯 | Put/Call OI ratio, Put/Call volume ratio (via Yahoo Finance options) |
| 6 | Momentum Agent | ⚡ | ROC-10, 20-day breakout, consecutive candle streaks, short momentum |
| 7 | Risk Agent | 🛡️ | ATR stop loss (2×ATR), ATR target (3×ATR), volatility regime, R/R ratio |
| 8 | Judge Agent | ⚖️ | Collects all votes — only fires BUY/SELL at 6/8 consensus |

## API Endpoints
- `GET /api/health` — health check
- `GET /api/analyze/{symbol}` — full analysis (all 8 agents + judgment)
- `GET /api/quick/{symbol}` — quick signal summary
- `GET /api/chart/{symbol}?period=3mo` — OHLCV + all indicator data for charting
- `GET /api/watchlist?symbols=AAPL,MSFT,...` — batch signals
- `GET /api/learning/weights` — agent accuracy + learned weights
- `GET /api/learning/history/{symbol}` — prediction history + outcomes
- `WS /api/ws/analyze/{symbol}` — live WebSocket stream (agent votes + judgment)

## Learning System (`trading/learning.py`)
- Every prediction saved to SQLite (`predictions.db`)
- 24h after prediction, system checks actual price vs predicted direction
- BUY correct if price went up ≥ 0.5%; SELL correct if down ≥ 0.5%
- Agent weights adjusted via Bayesian accuracy (0.5–1.5x range)
- Weights applied to confidence scores at runtime
- History visible in the dashboard "History" tab

## Signal Output Format
Each prediction includes:
- BUY / SELL / HOLD (fires at 6/8 consensus)
- Confidence % (weighted by learned agent accuracy)
- Entry price, Stop loss (2×ATR), Target price (3×ATR)
- Risk/Reward ratio
- Position size recommendation
- Which agents agreed / disagreed

## Chart Features
- Full candlestick chart (3-month default)
- EMA 9/21/50 overlays (toggleable)
- Bollinger Bands (toggleable)
- Signal markers with entry/stop/target price lines
- Bottom panel: Volume | RSI (with overbought/oversold) | MACD histogram

## Files
- `artifacts/api-server/trading/server.py` — FastAPI routes + WebSocket
- `artifacts/api-server/trading/agents.py` — All 8 agent implementations
- `artifacts/api-server/trading/learning.py` — Learning/tracking system
- `artifacts/api-server/trading/predictions.db` — SQLite learning database (auto-created)
- `artifacts/mockup-sandbox/src/components/mockups/TradingDashboard.tsx` — Full dashboard UI

## Workflows
- `artifacts/api-server: API Server` — Python FastAPI on PORT (port 8080)
- `artifacts/mockup-sandbox: Component Preview Server` — React Vite on PORT (port 8081)
