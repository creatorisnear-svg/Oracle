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
- BUY_CALL / BUY_PUT / HOLD (fires at 5/9 consensus)
- Confidence % (calibrated by consensus strength + overextension)
- Entry price, Stop loss & Target price — sized to volatility regime:
  - low-vol (ATR <1.5%): wider 1.4×ATR stop, closer 1.0×ATR target
  - normal: 1.1×ATR stop, 1.3×ATR target
  - high-vol (ATR >4%): tighter 0.9×ATR stop, wider 1.6×ATR target
- Forecast line: anchored at current price, scaled by confidence, real Mon–Fri sessions
- Overextension veto: signals are downgraded to HOLD when RSI ≥72/≤28 or price outside Bollinger band (avoids chasing tops/bottoms)
- Risk/Reward ratio, position size, agreed/disagreed agents

## Back-Testing & Tests
- `python3 artifacts/api-server/trading/tests/test_agents.py` — 46 unit tests for all 9 agents + Judge + Kelly + track record
- `python3 artifacts/api-server/trading/tests/backtest.py [SYMBOLS...]` — directional accuracy + target hit + forecast MAE per stock
- `python3 artifacts/api-server/trading/tests/compute_regime_stats.py` — manual re-derive of `regime_stats.json` (also runs automatically — see below)
- `python3 artifacts/api-server/trading/tests/compute_track_record.py` — manual re-derive of `track_record.json` (also runs automatically — see below)

## Auto-Refresh of Data Files (`auto_refresh.py`)
`track_record.json` and `regime_stats.json` are **local-only** (gitignored) so they survive `git pull` without merge conflicts and accumulate per-machine learning. `predictions.db` is also local-only.

On server startup the FastAPI lifespan hook schedules a background task that:
1. Checks both files; if missing or older than 7 days, regenerates them in a thread executor (~1-2 min each, non-blocking).
2. After regeneration, hot-reloads the in-memory caches (`agents.reload_track_record()`, `kelly.reload_stats()`) — no restart needed.
3. Sleeps 24 h, then repeats.

Admin endpoints for manual control:
- `GET  /api/admin/refresh/status` — file ages, last-refresh timestamps, stale flags
- `POST /api/admin/refresh/track-record?force=true` — regenerate now
- `POST /api/admin/refresh/regime-stats?force=true` — regenerate now

One-time setup on a new clone: run `artifacts/api-server/trading/untrack-local-data.bat` (Windows) to remove the stale tracked copies from git's index.

## Per-Stock Track Record (`track_record.json`)
The model's hit rate varies dramatically by symbol:
- **Strong (≥65%)**: META 80%, SPY 71%, MSFT 70%, QQQ 59% — model has genuine edge
- **Weak (45-55%)**: GOOGL 56%, TSLA/AMZN 50%, AAPL 44% — coin-flip territory
- **Poor (<45%)**: NVDA 12.5% — model historically wrong here

The dashboard shows this honestly so users don't act on misleading confidence on stocks where the model has no edge. When the historical track record is poor, the displayed confidence is also automatically capped at 55%.

## Kelly Position Sizer (`kelly.py`)
Every signal includes a `kelly` field with regime-aware position sizing:
- Volatility regime (low/normal/high) determined by ATR%
- Win probability blended 65% from back-test history of that regime + 35% from current confidence
- Half-Kelly applied for safety, capped at 10% of bankroll
- Returns `kelly_pct`, `dollars_per_10k`, `regime`, `explanation`

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
