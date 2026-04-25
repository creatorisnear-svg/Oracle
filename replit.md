# TradeSignal AI — 9-Agent Trading Prediction System v6.2.1

## Bug fixes on top of v6.2 (v6.2.1)
Three real bugs surfaced by the v6.2 audit, all fixed:

1. **IV/RV ratio was inflated 2.5×–8.8× on intraday/swing horizons.**
   `indicators.volatility_20d` always annualises with `sqrt(252)` regardless
   of bar interval. For swing (hourly bars) and intraday (5-min bars) it
   under-reports realised vol by `sqrt(6.5)` and `sqrt(78)` respectively,
   so my v6.2 IV/RV gate was firing "options expensive" on basically every
   swing/intraday signal. **Fix:** OptionsFlowAgent now fetches a fresh
   `period="2mo", interval="1d"` history and computes RV itself, so it's
   always comparing apples-to-apples with yfinance's annualised IV. Live
   AAPL test confirms ratio dropped from 5.54 → 0.97.

2. **Veto reasons were silently wiped before reaching the UI.** The
   earnings veto, chop filter, and weekly-counter-trend filter all set
   `evidence_reason = "..."` to surface their reason — but a later
   `evidence_reason = None` reset (start of the evidence-pillar gate
   block) wiped it before the response was assembled. The vetoes still
   **worked** (signal became HOLD), but the user saw "⏳ Need 5/9"
   instead of "🛑 NO BACKING: earnings in 1.2d — binary event…".
   **Fix:** moved `evidence_reason` and `evidence_pillars` initialisation
   to the top of `JudgeAgent.decide()` so earlier veto blocks can populate
   them safely.

3. **`agreed_agents` / `disagreed_agents` stayed populated after a
   HOLD veto.** Whenever any veto (overextension, chop, weekly trend,
   earnings, conviction-dominance) flipped `signal → HOLD`, the original
   pre-veto agreed/disagreed lists carried through. Result: UI showed
   "BUY_CALL agreed by 6 agents" alongside `signal: HOLD` — internally
   inconsistent. **Fix:** single cleanup block right before the
   evidence-pillar gate clears both lists when `signal == "HOLD"`.

## Accuracy upgrades (v6.2)
Four targeted fixes to known weaknesses in the agent stack:

1. **Earnings-proximity veto** — `_earnings_proximity(sym)` in `server.py`
   pulls the next earnings date from `yf.Ticker.calendar` /
   `ticker.earnings_dates` and stuffs `{days_until, in_danger, in_caution,
   date}` into `ind["earnings"]`. The Judge HOLDs any directional signal
   in the 0–2 day danger window (binary event + IV crush risk) and trims
   confidence by 15% in the 3–5 day caution window. Surfaced in the
   judgment payload at `macro_context.earnings`.

2. **OptionsFlowAgent IV/RV gate (IVR proxy)** — computes ATM implied
   vol (median across strikes within ±7% of spot) and divides by 20-day
   realized vol from indicators. Long options pay vega, so when IV is
   rich vs realized the premium is structurally too expensive. Trims
   confidence 20% when ratio ≥ 1.6, 10% when ≥ 1.3, boosts 5% when ≤ 0.8.
   Exposed on the vote as `iv_atm_pct` and `iv_rv_ratio`.

3. **SentimentAgent — negation + recency** — adds a NEGATIONS list and a
   `_is_negated()` window check (looks back 22 chars, forward 30 chars
   for "erased / halted / reversed" post-modifiers) so headlines like
   "gains erased" or "no longer bullish" no longer score positive.
   Recency weighting via `_recency_weight()` half-lifes news at 24h
   (0h → 1.6×, 6h → 1.2×, 24h → 0.8×, 72h → 0.45×) using
   `providerPublishTime` / `published_at`.

4. **PoliticalAgent bigram fix** — replaced bare-token matches like
   `"china"` and `"tariff"` (which both bullish and bearish phrasings
   matched to noise) with disambiguated bigrams: `"china tariff"`,
   `"china deal"`, `"new tariff"`, `"tariff removed"`, etc. Same
   `_negated()` window check applied here too.

## Paper Trading (v6.1)
A simulated trading account is now wired into the system. Every signal can be
"paper traded" with one click and the system tracks live mark-to-market P/L.

**Backend** (`paper_trading.py`)
- SQLite tables `paper_account` (single-row balance) + `paper_positions`.
- Default starting balance: **$10,000**. Each trade uses ~10% of available cash.
- Live mark-to-market via `_live_price()` in `server.py` (yfinance fast_info).
- **Auto-close**: positions whose target/stop is hit are closed on every refresh.

**Endpoints**
- `GET /api/paper/account` — equity, cash, held value, realized + unrealized P/L, win rate.
- `GET /api/paper/positions?status=open|closed|all` — live-marked positions.
- `POST /api/paper/open {symbol, signal, horizon, target, stop, confidence}` — opens a position.
- `POST /api/paper/close/{id}` — manual close at current price.
- `POST /api/paper/reset` — wipe positions, restore $10k.

**Frontend**
- New **PAPER 💰** tab with: account header (equity/return/cash/held/unrealized),
  open positions list (live P/L every 20s), closed history with win rate.
- **"Paper Trade This CALL/PUT"** button on the SIGNAL tab — one-click execution.
- Toast notifications for every open/close/auto-close event.

# TradeSignal AI — 9-Agent Trading Prediction System v6

## Overview
Professional trading prediction system with 9 specialized AI agents running in parallel, interactive TradingView-quality charts, live options chain viewer, buy/sell volume histogram, real options expiry dates, S-curve forecast lines, and a learning system that tracks prediction accuracy over time.

## Accuracy Boosters (v6)
Three new gates on top of the existing chop / weekly-trend / SPY-context filters:

1. **Conviction-dominance veto** — winning camp's TOTAL confidence weight must
   beat losing camp's by ≥1.25×, else HOLD. Prevents a 6×51% camp from
   overruling a 3×90% camp.
2. **Per-horizon higher-timeframe (HTF) tilt** — fetches the next-larger
   timeframe (intraday → 1h, swing → 1d, position → 1wk), computes EMA20/50
   trend, applies up to ±15% confidence based on alignment. Soft tilt (no
   hard veto) — back-testing showed strong-trend vetoes killed profitable
   mean-reversion trades.
3. **Honest blended confidence** — final headline number is
   `0.60 × volatility-aware target-hit prob + 0.40 × multiplier-adjusted vote conf`,
   capped at 90%. Prior cap was 95% and saturated on every swing setup; the
   new spread (54-90%) is a real probability the user can compare across
   trades. The cap reflects the actual ceiling from back-testing
   (~73-87% directional, ~80-87% target-hit on swing).

## Prediction Horizons (v5 — short-term focus for calls/puts)
The user can pick the prediction length from the UI. The chosen horizon drives the
data interval, the forecast window, the consensus threshold, and the ATR target /
stop multipliers fed to every agent.

| Key | Label | Bars | Lookahead | Threshold | Best for |
|-----|-------|------|-----------|-----------|----------|
| `intraday` | Intraday (1–2h) | 5-min | 24 bars (~2h) | 6/9 | scalps, 0DTE |
| `day` | Today (0DTE) | 15-min | 16 bars (~4h) | 6/9 | 0DTE / weekly |
| `swing` | Swing (1–5d) **(default)** | 1-hour | 30 bars (~5d) | 5/9 | weekly / 2-week |
| `position` | Position (1–3w) | daily | 7 bars (~7d) | 5/9 | monthly |

Defined in `agents.py::HORIZONS`. Endpoints: `GET /api/horizons`, then pass
`?horizon=swing` to `/api/analyze/{sym}` and the WebSocket
`/api/ws/analyze/{sym}?horizon=swing`.

## Back-Test Results (v5, `tests/backtest_horizons.py`)
On `AAPL NVDA SPY` (60 days of intraday history, ~15 sample points per horizon):

| Horizon | Signals | Direction | Target Hit | Avg Move |
|---------|---------|-----------|------------|----------|
| Swing (1–5d) | 14 | **78.6%** | **85.7%** | +1.96% |
| Position (1–3w) | 12 | 33.3% | 33.3% | −0.89% |
| Intraday / Day | 0 | — | — | — (gates too strict on noisy 5/15-min bars; intentional) |

**Take-away**: the swing horizon is the system's strongest setup. The strict
6-of-9 gate on intraday/0DTE is intentionally suppressing low-conviction trades.

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
