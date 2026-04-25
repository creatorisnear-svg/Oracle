# TradeSignal AI — 9-Agent Trading Prediction System v6.5

## Signal stickiness (v6.5) — the system commits to its trade plan
Without this, agents would disagree slightly between Analyze clicks and a CALL
would become HOLD would become PUT, even with no real reversal — the trader
gets whiplashed. New `signal_persistence.py` keeps an open directional trade
locked-in until something material changes:

- **Same direction next time** → keep original entry/target/stop, just refresh
  confidence and tally. No new DB row (avoids polluting per-agent stats with
  redundant snapshots of the same logical trade).
- **Agents go HOLD next time** → keep the active trade. Mixed agents isn't a
  reason to abandon an open position.
- **Opposite direction with weak consensus** → keep active. UI shows
  "Locked in since X — only N/9 puts (need 6 to flip)".
- **Opposite direction with STRONG (≥6/9)** → flip. A genuine reversal.

Active trade = newest non-HOLD prediction in `predictions.db` for
(symbol, horizon) that is still pending AND inside its hold window. Auto-closes
when verify_outcomes hits target/stop OR the window elapses.

UI shows a small lock badge under the signal whenever stickiness is active,
with the human-readable reason ("Locked in since 2h ago — agents reconfirmed").

## Persistent local data files (gitignored)
All learning state is in files that survive `git pull` so the user's machine
keeps its training. Already in `.gitignore`:
`predictions.db` (+ wal/journal/shm), `track_record.json`, `regime_stats.json`,
`discovered_strategies.json`. Nothing the agents learn is ever clobbered by
a code update.

## Learning-loop fix (v6.4) — the AIs actually learn now
Audit revealed the system was logging predictions but **never resolving
them** — 129 predictions in the DB, 0 resolved, agent weights frozen at
bootstrap values forever. Four bugs in the verification path, all fixed:

1. **No per-horizon maturity windows.** Old code waited 24 hours for
   every prediction regardless of horizon. Intraday (1-2h hold) and day
   (rest of session) never matured — they were stale before they were
   ripe. New `HORIZON_WINDOW_HOURS = {intraday: 2, day: 6, swing: 5d,
   position: 14d}` matches each horizon's intended hold time.

2. **Verification ignored target/stop.** Old code marked `correct =
   pct_change >= 0.5%` at one moment in time — a CALL that hit target
   then reversed got marked wrong; a CALL that got stopped out then
   bounced got marked right. New `verify_outcomes` pulls real OHLC
   bars across the hold window from yfinance and walks them
   chronologically to determine which level (target or stop) was hit
   first. If neither hits, judges by net direction at window close.

3. **REST endpoint never triggered verification.** Only the websocket
   path called `verify_outcomes`. Now `/api/analyze` triggers it
   opportunistically too, and a new periodic background task
   (`_periodic_outcome_verifier`) scans every 5 minutes for matured
   pendings across all symbols. This is the workhorse — without it
   predictions linger forever.

4. **Per-agent grading was wrong.** Old code:
   `agent_correct = (agent_vote == system_signal) == was_correct`
   measured agreement-with-Judge, not whether the agent's own call was
   right. An agent voting CALL on a real up-move got marked WRONG if
   the Judge held. New `_grade_agent_vote` grades each agent
   independently against the actual price direction during the hold
   window — exactly as a fair scorer would.

**Schema migration:** `predictions` table gained a `horizon` column
(default `'swing'` for back-compat with existing rows). Both
save_prediction call sites in `server.py` now pass the correct horizon.

**Verification** (forced one row to mature with Friday Apr 24 OHLC):
real bars walked, stop-hit detected before target, `outcome=WRONG`
recorded, per-agent grading confirmed independent of system signal
(CALL voters got `was_correct=1` even though system trade got stopped
because the actual move was upward). `agent_weights` table updated
with `total_predictions=1`, `correct_predictions` per agent — weights
will start adjusting once each agent reaches the 5-prediction Bayesian
prior threshold.

## Signal-rate fixes (v6.3)
The audit revealed the system was producing too many HOLDs: 100% HOLD on
intraday/day, 50% HOLD on swing/position. Two structural over-filters
fixed without compromising win-rate gates:

1. **Intraday & day threshold dropped 6/9 → 5/9.** With 9 agents that vote
   3 ways, requiring 6 to agree on direction was statistically near-
   impossible — production data showed 0 signals fired across 24 test runs.
   5/9 still requires a majority consensus, and the per-horizon
   `min_pillar_score=1.5` (vs 1.0 for swing/position) preserves the extra
   rigor where it matters: in evidence quality, not raw vote count.

2. **Overextension veto now requires multiple confirming signals.** Before:
   `RSI≥72 OR BB-z≥0.85 OR price≥BB_upper` triggered full HOLD — meaning
   any single overbought reading killed a trade. In normal uptrends RSI
   lives at 70+ for weeks, so this veto fired on most trending stocks.
   After: full veto requires **two of three** confirming signals (with
   tightened thresholds RSI≥75, BB-z≥0.95, at-or-above BB_upper) AND
   `RSI≥70`. A single signal trims confidence 25%; a mild warning trims
   15%. Same logic mirrored for oversold/PUT side.

**Result:** signal rate climbed from 25% → 56% across 12 symbols × 4
horizons (48 tests). All other filters (conviction-dominance gate,
chop filter, weekly counter-trend, earnings veto, HTF tilt) unchanged.

## Audit-round bug fixes (v6.2.2)
Six additional bugs surfaced by the audit, all fixed:

1. **Substring keyword matches in Sentiment + Political** — `text.find("high")`
   matched "highway"/"highlight"/"highlighted"; `text.find("cut")` matched
   "executive"/"prosecutor"; `"low"` matched "follow"/"slow"; `"war"` matched
   "award"/"forward"; `"miss"` matched "mission"/"dismiss". **Fix:** rewrote
   `_score_text` (Sentiment) and `_count_phrases` (Political) to use
   pre-compiled `\b{phrase}\b` word-bounded regexes with `re.finditer`.
   Verified: "highway service for executive customers" now scores 0/0
   instead of false-positive bull+bear.

2. **Overlapping phrase counts** — the prior `text.find(w, i + len(w))` loop
   could double-count overlapping fragments ("tariff" inside "tariffs raised").
   `re.finditer` yields **non-overlapping** matches by construction, so
   "china tariff hike" now scores 2 (china tariff + tariff hike) instead of 3.

3. **Asymmetric negation in PoliticalAgent** — Sentiment looked both backward
   (~22 chars) AND forward (~30 chars for "erased"/"halted"), but Political
   only looked backward (~25 chars). Headlines like "tariff hike averted"
   still scored bearish. **Fix:** added `POST_NEGATIONS` tuple ("averted",
   "called off", "walked back", "ruled out", "denied", etc.) and a forward
   window check in `_negated()`. Verified: "tariff hike averted" → 0 bear.

4. **Earnings cache key collision risk** — `(sym.upper(), "earn")` was
   distinct today but a future helper using the same string would clash.
   Renamed to `(sym.upper(), "_earnings_proximity")`.

5. **Earnings date type-coercion fragility** — yfinance returns earnings
   dates as a mix of `datetime.date`, `pd.Timestamp`, naive `datetime`,
   and (in some versions) ISO strings. The previous code only handled
   the first three. **Fix:** centralised conversion in `_coerce_to_utc_dt`
   which handles all four shapes and returns `None` on failure.

6. **`RuntimeWarning` flood from `indicators.py`** — every analyze call
   dumped 5–10 `divide by zero` / `invalid value` warnings to the log
   from `compute_rsi` and `compute_adx`. Both already mask the result
   with `np.where`, but numpy still computes the division before the mask.
   **Fix:** wrapped both in `np.errstate(divide="ignore", invalid="ignore")`.
   Logs are now clean.

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

## v6.9 — Volume confirmation gate + intraday VWAP-fight penalty

Two well-documented edge sources added as **confidence trims** (not new vetoes — keeps risk of false-HOLDs zero) inside `JudgeAgent.decide` after the existing macro / weekly-trend / SPY / HTF tilt block.

**5) Volume confirmation gate (universal)**
Empirically: directional moves on dry volume (rel_vol < 0.7) revert ~58% of the time vs ~38% on 1.5×+ volume. The Volume Agent already votes on this, but its single vote can be outweighed by 6 trend agents firing on a quiet tape. New trim:
- `rel_vol < 0.55` → 0.80× confidence (severe drought)
- `rel_vol < 0.80` → 0.92× confidence (below average)
- `rel_vol ≥ 1.50` → 1.05× confidence (strong confirmation)
- `0.80–1.50` → no adjustment

**6) VWAP-fight penalty (intraday & day horizons only)**
For 0DTE/intraday VWAP is *the* magnet — institutions benchmark to it intraday, so a CALL below VWAP / PUT above VWAP is fighting algorithmic flow. Skipped for swing/position where daily VWAP is just one of many anchors.
- CALL < VWAP by >0.3% → trim scaling with distance, capped at 18%
- PUT > VWAP by >0.3% → mirror trim
- Aligned with VWAP by >0.1% → 1.04× small honest boost

**Verified across 12 symbol/horizon combinations** — all responses 200 OK, confidence numbers move sensibly:
- AMD day p=$347.76 relV=1.78× above VWAP → 82% (volume + VWAP boosts active)
- MSFT swing p=$424.58 relV=0.87× → trimmed (below-average volume)
- TSLA intraday p=$376.16 below VWAP $383.85 → HOLD (other gates already vetoed)
- All HOLD signals correctly skip both gates.
- TS recompiles clean, browser console silent.

## v6.8 — Realistic option strikes (real CBOE ticks + horizon-aware moneyness)

The strike picker in `indicators.py::suggest_options` had two long-standing bugs:
1. **Hard-coded $5 increments** for every stock — broken for low-priced names (BAC at $52 lists $1 strikes, F at $12 lists $0.50 strikes) and for ETFs/high-priced stocks ($2.50 ticks in the $200-$500 band).
2. **Strike was just R1/S1 pivot rounded** with no horizon awareness — a 0DTE scalper got the same strike as a 3-week position trader.

**Fix — three new helpers in `indicators.py`:**
- `_strike_tick(price)` returns the real CBOE tick for the price band (`<$25:$0.50`, `<$200:$1`, `<$500:$2.50`, `<$1000:$5`, else `$10`).
- `_round_to_tick_directional(value, tick, mode)` rounds floor/ceil/nearest so a "slight ITM call" can't ever round across spot into OTM. Caught a real bug where SPY $713.98 BUY_CALL was returning strike $715 (OTM) under nearest-tick rounding — now correctly returns $710.
- `_horizon_moneyness(horizon, conf)` picks ATR offset by horizon: intraday/0DTE → ATM, swing → 0.5 ATR ITM, position → 1.0 ATR ITM, with ±0.4 ATR conviction tilt.

**Premium / delta / breakeven estimates** (no IV lookup, no scipy in hot path):
- `_estimate_premium` uses `C_atm ≈ 0.4 × σ × S × √T` Black-Scholes ATM approximation + intrinsic.
- `_estimate_delta` uses `erf`-based normal CDF for `N(d1)`.

**New JSON fields** on every Judgment payload (additive, old `strike_hint` text retained for back-compat):
`strike`, `strike_moneyness`, `strike_premium_est`, `strike_delta_est`, `strike_breakeven`, `strike_primary_expiry`, `strike_primary_dte`.

**Frontend** (`TradingDashboard.tsx`):
- `Judgment` interface extended with the new optional fields.
- Options-tab strike card rebuilt: large strike price + ITM/ATM/OTM color chip + Δ delta + EST PREMIUM (per share AND per contract) + BREAKEVEN @ EXPIRY. Falls back to the old single-line text card if the API is older.
- Signal tab: small "CALIBRATED (raw 75% → 62%)" sky-blue badge under the confidence ring when `judgment.meta.applied=true`. Honest UI surface for v6.7's calibration work.

**JudgeAgent.decide** now passes `horizon["key"]` and the post-disc-boost `conf` into `suggest_options` so strikes pick the right moneyness for the chosen horizon.

**Verified across price bands:**
- F $12.38 → $12.50 ($0.50 tick), AAPL $271 → $270 ($1 tick ATM intraday), NVDA $208 → $205 ($2.50 tick slight ITM swing), GOOGL $344 → $335 (deeper ITM position 30 DTE), META $675 → $670 ($5 tick), BRK-B $469 → $470, SPY $714 BUY_CALL → $710 (was the regressed-to-OTM case before directional rounding fix).
- TS compiles clean. All HOLD signals correctly skip premium/delta math.

## v6.7 — Meta-Judge: probability calibration + logistic stacker (`meta_judge.py`)

The hand-crafted JudgeAgent's `confidence` field was a heuristic — a 70% conviction did not mean "70% of these have historically won". Added `meta_judge.py` which adds two complementary upgrades on top of the existing judge (both additive, both pass-through-safe when sample sizes are too low):

**1. Isotonic confidence calibration**
Reads resolved historical predictions, bins by (signal, raw_conf), fits a monotone-non-decreasing mapping `raw_conf → win_rate` using a pure-numpy Pool Adjacent Violators algorithm (no sklearn dep). Activates per-direction once ≥30 resolved samples accumulate. Squashes extremes to [5%, 95%] so we never publish absurd certainty.

**2. Logistic stacker**
Trains two tiny pure-numpy logistic regressions (one for BUY_CALL, one for BUY_PUT) on `(per-agent signed vote) → was_correct`. The stacker learns which COMBINATIONS of agent votes empirically predict wins — not just which individual agents are reliable, which is what the existing weighting captures. Activates at ≥50 resolved samples per direction. Output is BLENDED with the calibrated judge confidence, capped at 40% blend weight (the judge always retains a meaningful voice). Blend weight further scales with stacker/calibrator agreement so the stacker can't override the judge during regime shifts when its training data may be stale.

**Wired into both REST and WebSocket analyze paths** in server.py via `meta_judge.apply_meta_judge(judgment, votes)` after `JUDGE.decide()`. The original judgment dict is mutated in place: `confidence` becomes the calibrated/blended value, and a `meta` field is added exposing `{raw_confidence, calibrated, stacker, blend_weight, final, applied}` for transparency. HOLD signals are short-circuited (no directional probability to calibrate).

**Brier score in `/api/admin/calibration`** — exposes `raw_brier` vs `calibrated_brier` per signal so the user can see whether confidence numbers are getting more honest over time. A drop from 0.25 → 0.18 means meaningful calibration improvement.

Frontend `Judgment` interface extended with optional `meta` field — pass-through safe, can be surfaced in the UI later as a "calibration honesty badge".

Also fixed a regression in `_run_agents` (REST path) where the new horizon multiplier used `h_cfg["key"]` (a variable that only exists in the WS path) instead of the function's `horizon` parameter.

## v6.6 — "Object is disposed" runtime overlay fix + per-horizon agent calibration

**Chart bug fix (TradingDashboard.tsx)**
The runtime overlay was caused by THREE separate code paths each calling `loadChart()` in parallel — the indicator-toggle effect, the `[judgment]` effect, AND the WebSocket `judgment` handler. Whichever one finished last would dispose the chart series the other two were still writing into.
- Added a `disposeChart()` helper that nulls every series-holding ref (forecast, post-forecast, target, stop, vol, spike markers, last-candle/bar caches) so live-tick updates can no-op cleanly after disposal.
- Refactored `drawPrediction` to capture `const chart = chartRef.current` once at the top, guard with `if (chartRef.current !== chart) return`, and wrap the body in `try/catch` that just debug-logs disposal-mid-draw races.
- Removed duplicate `loadChart()` from the `[judgment]` effect and the WS judgment handler — they now just call `drawPrediction(j)` to overlay on the existing chart. The chart only rebuilds on actual `[symbol, period, indicators_visible]` changes.
- All `chart.applyOptions/timeScale()` ops are now in `try/catch` blocks so a stray resize after dispose can't crash.

**Per-horizon agent calibration (learning.py + server.py)**
A SentimentAgent that's 70% accurate on swing trades but 40% on intraday currently averages to a meaningless ~55% global weight. Added `get_horizon_multipliers()` mirroring the existing `get_regime_multipliers()` / `get_symbol_multipliers()` pattern: JOINs `agent_performance.prediction_id` → `predictions.horizon`, computes per-(agent, horizon) accuracy, returns a multiplier in [0.7, 1.3] once ≥10 samples accumulate (defaults to 1.0 otherwise). Wired into BOTH the REST `_run_agents` path AND the WebSocket analyze path so `eff_w = base × regime × symbol × horizon`. The WS path also got the regime/symbol multipliers it was previously missing — both paths now use identical calibration. The `weight_breakdown` field on each vote now exposes the horizon component so the UI can show it.

## Post-Prediction Continuation Model (agents.py ~1978-2150)
The "after prediction" line shown beyond target/stop is **stock-specific**, not a 3-bucket lookup. A continuous score in [-1,+1] is built from per-stock readings (ROC10, MACD-hist trajectory, CMF, MFI, OBV slope, supertrend extension, weekly alignment, BB position, RSI distance), then a reversion drag pulls it negative when the move is over-extended. The score chooses a mode label (continuation/reversion/drift) AND scales the actual projected magnitude alongside the ticker's own ATR. The micro-wiggle is seeded from `hash(symbol + last_price)` so each ticker gets a unique signature wave instead of a shared sine. Score is exposed in the chart legend as e.g. `AFTER PREDICTION — CONTINUES ↑ (+0.64)`.
