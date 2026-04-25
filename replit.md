# TradeSignal AI — 9-Agent Trading Prediction System v4

## Overview
Professional trading prediction system with 9 specialized AI agents running in parallel, interactive TradingView-quality charts, live options chain viewer, buy/sell volume histogram, real options expiry dates, S-curve forecast lines, and a learning system that tracks prediction accuracy over time.

## Architecture
- **Backend**: Python FastAPI (`artifacts/api-server/trading/`)
- **Frontend**: React + Vite + Tailwind dashboard (`artifacts/mockup-sandbox/src/`)
- **Charts**: TradingView Lightweight Charts v5 (candlestick, EMA, BB, VWAP, SuperTrend, volume histogram)
- **Data**: Yahoo Finance via `yfinance`
- **Learning**: SQLite database tracking predictions & adjusting agent weights over time
- **Real-time**: WebSocket — all 9 agents run in parallel, results sent in one combined message

## Local Development (Windows)
Run this single command from the project root after `git pull`:
```
git pull && start cmd /k "cd artifacts/api-server && set PORT=8080 && npm run dev" && start cmd /k "cd artifacts/mockup-sandbox && set PORT=8081 && set BASE_PATH=/ && pnpm dev"
```
Then open `http://localhost:8081` in your browser.

## The 9 Agents (`trading/agents.py`)

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
- `GET /api/fear-greed?nocache=1` — market-wide Fear & Greed (60-second cache; pass `nocache=1` to force refresh)
- `GET /api/stock-sentiment/{symbol}` — per-ticker sentiment 0-100 from RSI, VWAP, trend, volume, MACD
- `GET /api/search?q=app&limit=8` — ticker autocomplete (Yahoo Finance lookup) used by the dashboard search box
- `WS /api/ws/analyze/{symbol}` — live WebSocket stream (agent votes + judgment + per-stock sentiment)

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
