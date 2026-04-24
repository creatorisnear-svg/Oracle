# TradeSignal AI — 8-Agent Trading Prediction System

## Overview
A trading prediction system with 8 specialized AI agents that vote on BUY/SELL/HOLD signals.
A signal only fires when **6 out of 8 agents agree**.

## Architecture
- **Backend**: Python FastAPI (`artifacts/api-server/trading/server.py`)
- **Frontend**: React + Vite dashboard (`artifacts/mockup-sandbox/src/`)
- **Data**: Yahoo Finance via `yfinance`
- **Indicators**: `ta` library (RSI, MACD, Bollinger Bands, EMA, OBV)
- **Real-time**: WebSocket streaming for live agent vote reveals

## The 8 Agents (`artifacts/api-server/trading/agents.py`)

| # | Agent | Emoji | Focus |
|---|-------|-------|-------|
| 1 | Price Action Agent | 📊 | Candlestick patterns, engulfing, hammers, trend structure |
| 2 | Technical Agent | 📈 | RSI, MACD, Bollinger Bands, EMA crossovers |
| 3 | Volume Agent | 📦 | Volume spikes, OBV, Money Flow Index |
| 4 | Sentiment Agent | 📰 | News headline scoring (bullish/bearish keywords) |
| 5 | Options Flow Agent | 🎯 | Put/Call ratio, open interest, unusual activity |
| 6 | Momentum Agent | ⚡ | ROC-10, breakouts, streak analysis, short momentum |
| 7 | Risk Agent | 🛡️ | ATR stop loss, position sizing, volatility regime |
| 8 | Judge Agent | ⚖️ | Collects all votes, fires BUY/SELL only at 6/8 consensus |

## API Endpoints
- `GET /api/health` — health check
- `GET /api/analyze/{symbol}` — full analysis with all agent details
- `GET /api/quick/{symbol}` — quick signal summary
- `GET /api/watchlist?symbols=AAPL,MSFT,...` — multi-symbol quick signals
- `WS /api/ws/analyze/{symbol}` — WebSocket stream (live agent voting)

## Signal Output
Every prediction includes:
- BUY / SELL / HOLD signal
- Confidence percentage
- Entry price
- Stop loss price (ATR-based)
- Target price (3x ATR)
- Which agents agreed / disagreed
- Vote tally (BUY/SELL/HOLD counts)
- Risk/Reward ratio
- Position size recommendation

## Workflows
- `artifacts/api-server: API Server` — Python FastAPI backend on PORT
- `artifacts/mockup-sandbox: Component Preview Server` — React frontend on PORT

## Key Files
- `artifacts/api-server/trading/server.py` — FastAPI routes + WebSocket
- `artifacts/api-server/trading/agents.py` — All 8 agent implementations
- `artifacts/mockup-sandbox/src/components/mockups/TradingDashboard.tsx` — Live dashboard UI
- `artifacts/mockup-sandbox/src/App.tsx` — Root entry (shows dashboard by default)
