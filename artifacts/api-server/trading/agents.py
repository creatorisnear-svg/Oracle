"""
9 Trading Prediction Agents + Judge  (v4 — Full Indicator Suite)
Signals: BUY_CALL | BUY_PUT | HOLD
New agents: Fear & Greed Agent (VIX/Put-Call/momentum) + Political/Trump Agent (Google News RSS)
Judge fires at 6/9 consensus (67% agreement required)
"""
import numpy as np
import pandas as pd
import urllib.request
import xml.etree.ElementTree as ET
import html as html_lib
import logging
import time

from indicators import (
    compute_all_indicators, score_indicators_to_direction,
    suggest_options, safe_float,
    compute_supertrend, compute_stochastic, compute_rsi, compute_vwap,
    compute_adx, compute_williams_r, compute_ichimoku, compute_fibonacci,
    compute_pivot_levels, detect_rsi_divergence,
)

logger = logging.getLogger(__name__)
Signal = str  # "BUY_CALL" | "BUY_PUT" | "HOLD"


def _vote(agent_name, emoji, signal, confidence, reason, **extra):
    return {"agent": agent_name, "emoji": emoji, "vote": signal,
            "confidence": round(min(confidence, 97), 1), "reason": reason, **extra}


def _hold(agent_name, emoji, reason):
    return {"agent": agent_name, "emoji": emoji, "vote": "HOLD",
            "confidence": 50.0, "reason": reason}


# ─────────────────────────────────────────────────────────────────────────────
# 1. PRICE ACTION AGENT
# ─────────────────────────────────────────────────────────────────────────────
class PriceActionAgent:
    name = "Price Action Agent"
    emoji = "🕯️"
    method = "Candlestick patterns + SuperTrend + Pivot breakout analysis"

    def analyze(self, df, ind):
        try:
            closes = df["Close"].values
            opens = df["Open"].values
            highs = df["High"].values
            lows = df["Low"].values
            n = len(closes)
            if n < 10:
                return _hold(self.name, self.emoji, "Insufficient data")

            c, o, h, l = closes[-1], opens[-1], highs[-1], lows[-1]
            pc, po = closes[-2], opens[-2]
            body = abs(c - o)
            rng = h - l
            bp = body / rng if rng > 0 else 0
            uw = (h - max(c, o)) / rng if rng > 0 else 0
            lw = (min(c, o) - l) / rng if rng > 0 else 0

            signals, reasons = [], []

            # Candlestick patterns
            if c > o and pc < po and c > po and o < pc:
                signals.append("BUY_CALL"); reasons.append("Bullish engulfing")
            if c < o and pc > po and c < po and o > pc:
                signals.append("BUY_PUT"); reasons.append("Bearish engulfing")
            if lw > 0.6 and uw < 0.1 and c >= o:
                signals.append("BUY_CALL"); reasons.append("Hammer reversal")
            if uw > 0.6 and lw < 0.1 and c <= o:
                signals.append("BUY_PUT"); reasons.append("Shooting star")
            if c > o and bp > 0.65 and ind.get("rel_volume", 1) > 1.3:
                signals.append("BUY_CALL"); reasons.append(f"Strong bull candle {ind.get('rel_volume',1):.1f}×vol")
            if c < o and bp > 0.65 and ind.get("rel_volume", 1) > 1.3:
                signals.append("BUY_PUT"); reasons.append(f"Strong bear candle {ind.get('rel_volume',1):.1f}×vol")
            # Price structure
            if n >= 5:
                if highs[-1] > highs[-5] and lows[-1] > lows[-5]:
                    signals.append("BUY_CALL"); reasons.append("Higher highs + lows (uptrend)")
                elif highs[-1] < highs[-5] and lows[-1] < lows[-5]:
                    signals.append("BUY_PUT"); reasons.append("Lower highs + lows (downtrend)")
            # SuperTrend
            if ind.get("supertrend_dir") == "up":
                signals.append("BUY_CALL"); reasons.append(f"SuperTrend bullish")
            else:
                signals.append("BUY_PUT"); reasons.append("SuperTrend bearish")
            # Pivot breakout
            pivot_bias = ind.get("pivot_bias", "neutral")
            if pivot_bias == "bullish_breakout":
                signals.append("BUY_CALL"); reasons.append("Pivot R1 breakout")
            elif pivot_bias == "bearish_breakdown":
                signals.append("BUY_PUT"); reasons.append("Pivot S1 breakdown")
            # Ichimoku confirmation
            ichi = ind.get("ichimoku_signal", "neutral")
            if ichi == "bullish": signals.append("BUY_CALL"); reasons.append("Above Ichimoku cloud")
            elif ichi == "bearish": signals.append("BUY_PUT"); reasons.append("Below Ichimoku cloud")

            calls = signals.count("BUY_CALL")
            puts = signals.count("BUY_PUT")
            total = max(calls + puts, 1)
            conf = 55 + (max(calls, puts) / total) * 30
            r = " | ".join(reasons[:3])
            if calls > puts:
                return _vote(self.name, self.emoji, "BUY_CALL", conf, r)
            elif puts > calls:
                return _vote(self.name, self.emoji, "BUY_PUT", conf, r)
            return _hold(self.name, self.emoji, r or "No clear pattern")
        except Exception as e:
            return _hold(self.name, self.emoji, f"Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. TECHNICAL AGENT
# ─────────────────────────────────────────────────────────────────────────────
class TechnicalAgent:
    name = "Technical Agent"
    emoji = "📈"
    method = "RSI + MACD + Bollinger + SuperTrend + Stochastic + ADX + Williams %R + Ichimoku"

    def analyze(self, df, ind):
        try:
            result = score_indicators_to_direction(ind)
            direction = result["direction"]
            conf = result["confidence"]
            score = result["score"]

            adx = ind.get("adx", 20)
            rsi = ind.get("rsi14", 50)
            wr = ind.get("williams_r", -50)
            ichi = ind.get("ichimoku_signal", "neutral")
            div = ind.get("rsi_divergence", "none")

            reasons = [
                f"Score {score:+.1f}/12 | ADX {adx:.0f}({'TREND' if adx>25 else 'CHOP'})",
                f"RSI {rsi:.0f} | W%R {wr:.0f} | Ichimoku {ichi}",
                f"Divergence: {div}" if div != "none" else f"MACD hist {ind.get('macd_hist',0):+.4f}",
            ]

            # ADX filter: if market is choppy (ADX < 15), downgrade confidence
            if adx < 15 and direction != "NEUTRAL":
                conf *= 0.75
                reasons[0] += " [LOW ADX — caution]"

            if direction == "BULLISH":
                return _vote(self.name, self.emoji, "BUY_CALL", conf, " | ".join(reasons))
            elif direction == "BEARISH":
                return _vote(self.name, self.emoji, "BUY_PUT", conf, " | ".join(reasons))
            return _hold(self.name, self.emoji, f"Neutral score {score:+.1f}" + " | " + reasons[1])
        except Exception as e:
            return _hold(self.name, self.emoji, f"Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. VOLUME AGENT
# ─────────────────────────────────────────────────────────────────────────────
class VolumeAgent:
    name = "Volume Agent"
    emoji = "📦"
    method = "Relative volume + OBV slope + Up/Down vol ratio + Volume trend + VWAP confirmation"

    def analyze(self, df, ind):
        try:
            rel_v = ind.get("rel_volume", 1.0)
            obv_slope = ind.get("obv_slope_10d_pct", 0.0)
            up_dn = ind.get("up_dn_vol_ratio", 1.0)
            vol_trend = ind.get("vol_trend_5v20", 1.0)
            vc = ind.get("vol_confirms", "neutral")
            ch = ind.get("change_1d", 0)
            pvwap = ind.get("price_vs_vwap_pct", 0)

            signals, reasons = [], []

            if rel_v > 2.0:
                reasons.append(f"Volume surge {rel_v:.1f}×")
                signals.append("BUY_CALL" if ch > 0 else "BUY_PUT")
            elif rel_v > 1.5:
                reasons.append(f"Above-avg vol {rel_v:.1f}×")
                signals.append("BUY_CALL" if ch > 0 else "BUY_PUT")
            if obv_slope > 8:
                signals.append("BUY_CALL"); reasons.append(f"OBV +{obv_slope:.0f}% (accumulation)")
            elif obv_slope < -8:
                signals.append("BUY_PUT"); reasons.append(f"OBV {obv_slope:.0f}% (distribution)")
            if up_dn > 1.4:
                signals.append("BUY_CALL"); reasons.append(f"Accumulation ratio {up_dn:.2f}×")
            elif up_dn < 0.7:
                signals.append("BUY_PUT"); reasons.append(f"Distribution ratio {up_dn:.2f}×")
            if vol_trend > 1.3:
                reasons.append(f"Volume building {vol_trend:.1f}×")
                signals.append("BUY_CALL" if ch > 0 else "BUY_PUT")
            if vc == "confirms" and ch > 0:
                signals.append("BUY_CALL"); reasons.append("Vol confirms bullish")
            elif vc == "confirms" and ch < 0:
                signals.append("BUY_PUT"); reasons.append("Vol confirms bearish")
            # VWAP
            if pvwap > 1.5:
                signals.append("BUY_CALL"); reasons.append(f"Price {pvwap:.1f}% above VWAP")
            elif pvwap < -1.5:
                signals.append("BUY_PUT"); reasons.append(f"Price {pvwap:.1f}% below VWAP")

            calls = signals.count("BUY_CALL")
            puts = signals.count("BUY_PUT")
            conf = 55 + min(rel_v * 6, 25)
            r = " | ".join(reasons[:3]) or f"Vol {rel_v:.1f}× neutral"
            if calls > puts:
                return _vote(self.name, self.emoji, "BUY_CALL", conf, r)
            elif puts > calls:
                return _vote(self.name, self.emoji, "BUY_PUT", conf, r)
            return _hold(self.name, self.emoji, r)
        except Exception as e:
            return _hold(self.name, self.emoji, f"Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. SENTIMENT AGENT
# ─────────────────────────────────────────────────────────────────────────────
class SentimentAgent:
    name = "Sentiment Agent"
    emoji = "📰"
    method = "Yahoo Finance news headline NLP scoring + 5-day price trend proxy"

    BULLISH = ["surge","rally","gain","rise","jump","soar","beat","exceed","record","high","growth",
               "profit","upgrade","bullish","positive","strong","boost","breakout","outperform",
               "partnership","acquisition","innovation","revenue","approval","launch","deal",
               "raises","guidance","recovery","expansion","beats","raises","dividend"]
    BEARISH = ["drop","fall","crash","decline","plunge","miss","loss","sell","downgrade","bearish",
               "negative","weak","cut","layoff","risk","concern","warn","volatile","uncertainty",
               "lawsuit","fraud","recall","shortage","debt","deficit","investigation","probe",
               "default","bankruptcy","missed","disappoints","suspended","warning","slump"]

    def analyze(self, df, ind):
        try:
            news = ind.get("_news", [])
            if not news:
                ch5 = ind.get("change_5d", 0)
                if ch5 > 3: return _vote(self.name, self.emoji, "BUY_CALL", 62, f"5D trend +{ch5:.1f}% (no live news)")
                elif ch5 < -3: return _vote(self.name, self.emoji, "BUY_PUT", 62, f"5D trend {ch5:.1f}% (no live news)")
                return _hold(self.name, self.emoji, "No news; price trend neutral")

            bull_score = bear_score = 0
            for item in news[:12]:
                title = (item.get("title", "") + " " + item.get("summary", "")).lower()
                for w in self.BULLISH:
                    if w in title: bull_score += 1
                for w in self.BEARISH:
                    if w in title: bear_score += 1

            total = bull_score + bear_score
            if total == 0:
                return _hold(self.name, self.emoji, "Neutral news sentiment")

            ratio = bull_score / total
            conf = 55 + abs(bull_score - bear_score) / max(total, 1) * 30
            if ratio > 0.6:
                return _vote(self.name, self.emoji, "BUY_CALL", conf, f"News bullish {bull_score}↑/{bear_score}↓")
            elif ratio < 0.4:
                return _vote(self.name, self.emoji, "BUY_PUT", conf, f"News bearish {bear_score}↓/{bull_score}↑")
            return _hold(self.name, self.emoji, f"Mixed news {bull_score}↑/{bear_score}↓")
        except Exception as e:
            return _hold(self.name, self.emoji, f"Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. OPTIONS FLOW AGENT
# ─────────────────────────────────────────────────────────────────────────────
class OptionsFlowAgent:
    name = "Options Flow Agent"
    emoji = "🎯"
    method = "Put/Call OI ratio + Put/Call volume ratio (real options chain from Yahoo Finance)"

    def analyze(self, df, ind):
        try:
            import yfinance as yf
            symbol = ind.get("_symbol", "")
            if not symbol:
                return _hold(self.name, self.emoji, "No symbol")
            ticker = yf.Ticker(symbol)
            dates = ticker.options
            if not dates:
                return _hold(self.name, self.emoji, "No options chain")

            chain0 = ticker.option_chain(dates[0])
            calls = chain0.calls
            puts = chain0.puts
            total_call_oi = safe_float(calls["openInterest"].sum()) if "openInterest" in calls.columns else 0
            total_put_oi = safe_float(puts["openInterest"].sum()) if "openInterest" in puts.columns else 0
            total_call_vol = safe_float(calls["volume"].sum()) if "volume" in calls.columns else 0
            total_put_vol = safe_float(puts["volume"].sum()) if "volume" in puts.columns else 0

            pc_oi = total_put_oi / total_call_oi if total_call_oi > 0 else 1
            pc_vol = total_put_vol / total_call_vol if total_call_vol > 0 else 1
            reasons = [f"P/C OI:{pc_oi:.2f}", f"P/C Vol:{pc_vol:.2f}"]

            if pc_vol < 0.5: return _vote(self.name, self.emoji, "BUY_CALL", 82, f"Heavy call flow — {' '.join(reasons)}")
            elif pc_vol < 0.7: return _vote(self.name, self.emoji, "BUY_CALL", 70, f"Call dominance — {' '.join(reasons)}")
            elif pc_vol > 2.0: return _vote(self.name, self.emoji, "BUY_PUT", 82, f"Heavy put flow — {' '.join(reasons)}")
            elif pc_vol > 1.4: return _vote(self.name, self.emoji, "BUY_PUT", 70, f"Put dominance — {' '.join(reasons)}")
            elif pc_oi < 0.7: return _vote(self.name, self.emoji, "BUY_CALL", 63, f"Call OI dominance — {' '.join(reasons)}")
            elif pc_oi > 1.3: return _vote(self.name, self.emoji, "BUY_PUT", 63, f"Put OI dominance — {' '.join(reasons)}")
            return _hold(self.name, self.emoji, f"Neutral flow — {' '.join(reasons)}")
        except Exception as e:
            return _hold(self.name, self.emoji, f"Options unavailable: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. MOMENTUM AGENT
# ─────────────────────────────────────────────────────────────────────────────
class MomentumAgent:
    name = "Momentum Agent"
    emoji = "⚡"
    method = "ROC-5/10 + 20D breakout + consecutive streak + VWAP deviation + ADX +DI/-DI"

    def analyze(self, df, ind):
        try:
            closes = df["Close"].values
            highs = df["High"].values
            lows = df["Low"].values
            n = len(closes)
            if n < 10:
                return _hold(self.name, self.emoji, "Not enough data")

            price = closes[-1]
            signals, reasons = [], []

            roc5 = (closes[-1] - closes[-5]) / closes[-5] * 100 if n >= 5 and closes[-5] > 0 else 0
            roc10 = (closes[-1] - closes[-10]) / closes[-10] * 100 if n >= 10 and closes[-10] > 0 else 0

            if roc5 > 2: signals.append("BUY_CALL"); reasons.append(f"ROC5 +{roc5:.1f}%")
            elif roc5 < -2: signals.append("BUY_PUT"); reasons.append(f"ROC5 {roc5:.1f}%")
            if roc10 > 4: signals.append("BUY_CALL"); reasons.append(f"ROC10 +{roc10:.1f}%")
            elif roc10 < -4: signals.append("BUY_PUT"); reasons.append(f"ROC10 {roc10:.1f}%")

            h20 = np.max(highs[-20:]) if n >= 20 else np.max(highs)
            l20 = np.min(lows[-20:]) if n >= 20 else np.min(lows)
            if price >= h20 * 0.99: signals.append("BUY_CALL"); reasons.append(f"20D breakout high ${h20:.2f}")
            elif price <= l20 * 1.01: signals.append("BUY_PUT"); reasons.append(f"20D breakdown low ${l20:.2f}")

            # Streak
            bull_streak = bear_streak = 0
            for i in range(n-1, max(n-7, 0), -1):
                if closes[i] > closes[i-1]: bull_streak += 1
                else: break
            for i in range(n-1, max(n-7, 0), -1):
                if closes[i] < closes[i-1]: bear_streak += 1
                else: break
            if bull_streak >= 3: signals.append("BUY_CALL"); reasons.append(f"{bull_streak}-day streak up")
            if bear_streak >= 3: signals.append("BUY_PUT"); reasons.append(f"{bear_streak}-day streak down")

            # VWAP
            pvwap = ind.get("price_vs_vwap_pct", 0)
            if pvwap > 1.0: signals.append("BUY_CALL"); reasons.append(f"VWAP +{pvwap:.1f}%")
            elif pvwap < -1.0: signals.append("BUY_PUT"); reasons.append(f"VWAP {pvwap:.1f}%")

            # ADX directional
            plus_di = ind.get("plus_di", 25)
            minus_di = ind.get("minus_di", 25)
            if plus_di > minus_di + 8: signals.append("BUY_CALL"); reasons.append(f"+DI({plus_di:.0f})>-DI({minus_di:.0f})")
            elif minus_di > plus_di + 8: signals.append("BUY_PUT"); reasons.append(f"-DI({minus_di:.0f})>+DI({plus_di:.0f})")

            calls = signals.count("BUY_CALL")
            puts = signals.count("BUY_PUT")
            total = max(calls + puts, 1)
            conf = 55 + (max(calls, puts) / total) * 32
            r = " | ".join(reasons[:3]) or f"ROC5:{roc5:+.1f}%"
            if calls > puts: return _vote(self.name, self.emoji, "BUY_CALL", conf, r)
            elif puts > calls: return _vote(self.name, self.emoji, "BUY_PUT", conf, r)
            return _hold(self.name, self.emoji, r)
        except Exception as e:
            return _hold(self.name, self.emoji, f"Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 7. RISK AGENT
# ─────────────────────────────────────────────────────────────────────────────
class RiskAgent:
    name = "Risk Agent"
    emoji = "🛡️"
    method = "ATR stop/target + volatility regime + 52W position + Fibonacci proximity + ADX filter"

    def analyze(self, df, ind):
        try:
            price = ind.get("price", df["Close"].iloc[-1])
            atr = ind.get("atr14", price * 0.02)
            vola = ind.get("volatility_20d", 25)
            adx = ind.get("adx", 20)

            signals, reasons = [], []

            if vola < 20:
                signals.append("BUY_CALL"); reasons.append(f"Low vol {vola:.0f}% — options cheap")
            elif vola > 50:
                signals.append("BUY_PUT"); reasons.append(f"Extreme vol {vola:.0f}% — premium elevated")
            else:
                reasons.append(f"Moderate vol {vola:.0f}%")

            atr_pct = atr / price * 100 if price > 0 else 2
            if atr_pct < 1.5: signals.append("BUY_CALL"); reasons.append(f"Tight ATR {atr_pct:.1f}%")
            elif atr_pct > 4: signals.append("BUY_PUT"); reasons.append(f"Wide ATR {atr_pct:.1f}%")

            h52 = ind.get("high_52w", 0); l52 = ind.get("low_52w", 0)
            if h52 > 0 and l52 > 0:
                pos52 = (price - l52) / (h52 - l52) * 100
                if pos52 > 85: reasons.append(f"Near 52W high ({pos52:.0f}%) — extended")
                elif pos52 < 15: reasons.append(f"Near 52W low ({pos52:.0f}%) — potential base")

            # Fibonacci proximity (golden ratio is high probability)
            fib618 = ind.get("fibonacci", {}).get("fib_618", 0)
            if fib618 and abs(price - fib618) / price < 0.015:
                signals.append("BUY_CALL"); reasons.append(f"At 61.8% Fib ${fib618:.2f} (golden ratio support)")

            # ADX: only trade strong trends
            if adx > 30: reasons.append(f"ADX {adx:.0f} — strong trend, good risk")
            elif adx < 18: reasons.append(f"ADX {adx:.0f} — choppy, reduce size")

            stop_long = round(price - 2 * atr, 2)
            stop_short = round(price + 2 * atr, 2)
            tgt_long = round(price + 3 * atr, 2)
            tgt_short = round(price - 3 * atr, 2)

            calls = signals.count("BUY_CALL")
            puts = signals.count("BUY_PUT")
            conf = 60 + abs(calls - puts) * 8
            base = {"stop_loss_long": stop_long, "stop_loss_short": stop_short,
                    "target_long": tgt_long, "target_short": tgt_short,
                    "atr": round(atr, 3), "volatility_pct": round(vola, 1)}
            r = " | ".join(reasons[:3])
            if calls > puts: return {**_vote(self.name, self.emoji, "BUY_CALL", conf, r), **base}
            elif puts > calls: return {**_vote(self.name, self.emoji, "BUY_PUT", conf, r), **base}
            return {**_hold(self.name, self.emoji, r), **base}
        except Exception as e:
            return {**_hold(self.name, self.emoji, f"Error: {e}"),
                    "stop_loss_long": 0, "stop_loss_short": 0,
                    "target_long": 0, "target_short": 0, "atr": 0, "volatility_pct": 25}


# ─────────────────────────────────────────────────────────────────────────────
# 8. FEAR & GREED AGENT  ← NEW
# ─────────────────────────────────────────────────────────────────────────────
class FearGreedAgent:
    name = "Fear & Greed Agent"
    emoji = "😱"
    method = "VIX level + SPY Put/Call ratio + SPY 125-day momentum = Fear/Greed score (0-100)"

    _cache: dict = {}
    _cache_ts: float = 0
    CACHE_TTL = 300  # 5 minutes

    def _compute_fear_greed(self) -> dict:
        """Compute CNN-style Fear & Greed Index from real market data."""
        if time.time() - self._cache_ts < self.CACHE_TTL and self._cache:
            return self._cache

        import yfinance as yf
        components = []
        score = 50.0  # default neutral

        # 1. VIX (40% weight — most important)
        try:
            vix_info = yf.Ticker("^VIX").fast_info
            vix = safe_float(getattr(vix_info, "last_price", None))
            if vix > 0:
                if vix < 12: vs = 92
                elif vix < 15: vs = 78
                elif vix < 20: vs = 63
                elif vix < 25: vs = 45
                elif vix < 30: vs = 30
                elif vix < 40: vs = 18
                else: vs = 8
                components.append({"name": "VIX", "score": vs, "value": round(vix, 2),
                                   "weight": 0.40, "label": f"VIX {vix:.1f}"})
        except Exception as e:
            logger.debug(f"VIX fetch: {e}")

        # 2. SPY momentum vs 125-day SMA (30% weight)
        try:
            spy_df = yf.Ticker("SPY").history(period="7mo", interval="1d")
            if len(spy_df) >= 125:
                sma125 = spy_df["Close"].rolling(125).mean().iloc[-1]
                curr = spy_df["Close"].iloc[-1]
                pct = (curr - sma125) / sma125 * 100
                if pct > 8: ms = 90
                elif pct > 4: ms = 75
                elif pct > 1: ms = 62
                elif pct > 0: ms = 53
                elif pct > -4: ms = 40
                elif pct > -8: ms = 25
                else: ms = 12
                components.append({"name": "SPY Momentum", "score": ms, "value": round(pct, 2),
                                   "weight": 0.30, "label": f"SPY vs 125SMA {pct:+.1f}%"})
        except Exception as e:
            logger.debug(f"SPY momentum: {e}")

        # 3. SPY Put/Call volume ratio (20% weight)
        try:
            spy_opts = yf.Ticker("SPY")
            dates = spy_opts.options
            if dates:
                chain = spy_opts.option_chain(dates[0])
                call_vol = safe_float(chain.calls["volume"].sum())
                put_vol = safe_float(chain.puts["volume"].sum())
                pc_vol = put_vol / call_vol if call_vol > 0 else 1
                if pc_vol < 0.5: ps = 90
                elif pc_vol < 0.7: ps = 75
                elif pc_vol < 0.9: ps = 60
                elif pc_vol < 1.1: ps = 45
                elif pc_vol < 1.5: ps = 30
                else: ps = 15
                components.append({"name": "Put/Call", "score": ps, "value": round(pc_vol, 3),
                                   "weight": 0.20, "label": f"P/C ratio {pc_vol:.2f}"})
        except Exception as e:
            logger.debug(f"P/C ratio: {e}")

        # 4. Junk bond proxy — HYG vs IEF performance (10% weight)
        try:
            hyg_df = yf.Ticker("HYG").history(period="5d", interval="1d")
            ief_df = yf.Ticker("IEF").history(period="5d", interval="1d")
            if len(hyg_df) >= 2 and len(ief_df) >= 2:
                hyg_ret = (hyg_df["Close"].iloc[-1] - hyg_df["Close"].iloc[-2]) / hyg_df["Close"].iloc[-2] * 100
                ief_ret = (ief_df["Close"].iloc[-1] - ief_df["Close"].iloc[-2]) / ief_df["Close"].iloc[-2] * 100
                spread = hyg_ret - ief_ret  # positive = risk-on (greed)
                if spread > 0.3: js = 80
                elif spread > 0: js = 62
                elif spread > -0.3: js = 45
                else: js = 25
                components.append({"name": "Junk Bond Demand", "score": js, "value": round(spread, 3),
                                   "weight": 0.10, "label": f"HYG-IEF spread {spread:+.3f}%"})
        except Exception as e:
            logger.debug(f"Junk bond: {e}")

        if components:
            total_weight = sum(c["weight"] for c in components)
            score = sum(c["score"] * c["weight"] for c in components) / total_weight

        score = round(score, 1)
        if score >= 75: label, color = "Extreme Greed", "#10b981"
        elif score >= 55: label, color = "Greed", "#22c55e"
        elif score >= 45: label, color = "Neutral", "#f59e0b"
        elif score >= 25: label, color = "Fear", "#f97316"
        else: label, color = "Extreme Fear", "#ef4444"

        result = {"score": score, "label": label, "color": color, "components": components}
        FearGreedAgent._cache = result
        FearGreedAgent._cache_ts = time.time()
        return result

    def analyze(self, df, ind):
        try:
            fg = self._compute_fear_greed()
            score = fg["score"]
            label = fg["label"]

            reasons = [f"Fear & Greed: {score}/100 — {label}"]
            for c in fg["components"][:2]:
                reasons.append(c["label"])

            # Trading signal based on score
            if score >= 75:
                # Extreme Greed = market overbought → contrarian PUT
                return _vote(self.name, self.emoji, "BUY_PUT", 74,
                             f"Extreme Greed ({score}) — contrarian bearish | " + " | ".join(reasons))
            elif score >= 60:
                # Greed = mild bullish (trend following)
                return _vote(self.name, self.emoji, "BUY_CALL", 65,
                             f"Greed ({score}) — trend continues bullish | " + " | ".join(reasons))
            elif score <= 25:
                # Extreme Fear = market oversold → contrarian CALL
                return _vote(self.name, self.emoji, "BUY_CALL", 78,
                             f"Extreme Fear ({score}) — contrarian bullish buy the dip | " + " | ".join(reasons))
            elif score <= 40:
                # Fear = mild bearish
                return _vote(self.name, self.emoji, "BUY_PUT", 62,
                             f"Fear ({score}) — market weak | " + " | ".join(reasons))
            return _hold(self.name, self.emoji, f"Neutral sentiment ({score}/100) — no edge")
        except Exception as e:
            return _hold(self.name, self.emoji, f"Fear/Greed unavailable: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 9. POLITICAL / TRUMP AGENT  ← NEW
# ─────────────────────────────────────────────────────────────────────────────
class PoliticalAgent:
    name = "Political Agent"
    emoji = "🏛️"
    method = "Google News RSS scraper for Trump + tariff + Fed + macro keywords — market impact scoring"

    MARKET_BULLISH = [
        "tax cut", "deregulation", "stimulus", "deal", "agreement", "rate cut",
        "rate hold", "pause", "eases", "boosts", "supports", "pro-business",
        "infrastructure", "signed", "passed", "boom", "recovery", "strong economy",
        "jobs added", "beats expectations", "trade deal", "ceasefire", "peace",
        "tariff removed", "exemption", "positive", "rally", "gains"
    ]
    MARKET_BEARISH = [
        "tariff", "trade war", "sanction", "ban", "escalate", "retaliate",
        "recession", "inflation", "hike", "rate hike", "hawkish", "tightening",
        "debt ceiling", "shutdown", "default", "war", "conflict", "tension",
        "investigation", "indicted", "impeach", "crisis", "crash", "plunge",
        "layoff", "bankruptcy", "losses", "miss", "disappoints", "uncertainty",
        "tariffs raised", "china", "new tariff", "additional tariff"
    ]

    _news_cache: list = []
    _cache_ts: float = 0
    CACHE_TTL = 120  # 2 minutes

    def _fetch_political_news(self) -> list:
        if time.time() - self._cache_ts < self.CACHE_TTL and self._cache:
            return PoliticalAgent._news_cache

        queries = [
            "trump tariff stock market economy",
            "federal reserve interest rate economy",
            "US economy recession inflation",
        ]
        all_news = []
        for q in queries[:2]:
            try:
                q_enc = q.replace(" ", "+")
                url = f"https://news.google.com/rss/search?q={q_enc}&hl=en-US&gl=US&ceid=US:en"
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; TradeSignalAI/1.0)"
                })
                with urllib.request.urlopen(req, timeout=6) as resp:
                    content = resp.read()
                root = ET.fromstring(content)
                for item in root.findall(".//item")[:6]:
                    title = html_lib.unescape(item.findtext("title", ""))
                    link = item.findtext("link", "")
                    pub = item.findtext("pubDate", "")
                    source_el = item.find("{http://purl.org/dc/elements/1.1/}creator")
                    source = source_el.text if source_el is not None else "Google News"
                    all_news.append({
                        "title": title,
                        "url": link,
                        "source": source,
                        "published_at": pub,
                        "category": "political",
                    })
            except Exception as e:
                logger.debug(f"Google News fetch failed: {e}")

        PoliticalAgent._news_cache = all_news
        PoliticalAgent._cache_ts = time.time()
        return all_news

    def analyze(self, df, ind):
        try:
            news = self._fetch_political_news()
            # Also check Yahoo Finance news for political keywords
            yahoo_news = ind.get("_news", [])
            all_news = news + yahoo_news

            if not all_news:
                return _hold(self.name, self.emoji, "No political news available")

            bull = bear = 0
            top_headlines = []
            for item in all_news[:15]:
                title = (item.get("title", "") + " " + item.get("summary", "")).lower()
                item_bull = sum(1 for w in self.MARKET_BULLISH if w in title)
                item_bear = sum(1 for w in self.MARKET_BEARISH if w in title)
                bull += item_bull
                bear += item_bear
                if item_bull > 0 or item_bear > 0:
                    top_headlines.append(item.get("title", "")[:60])

            total = bull + bear
            if total == 0:
                return _hold(self.name, self.emoji, "Politically neutral news cycle")

            ratio = bull / total
            conf = 54 + abs(bull - bear) / max(total, 1) * 28
            headline_str = top_headlines[0][:50] if top_headlines else ""

            if ratio > 0.62:
                return _vote(self.name, self.emoji, "BUY_CALL", conf,
                             f"Pro-market news {bull}↑/{bear}↓ | {headline_str}")
            elif ratio < 0.38:
                return _vote(self.name, self.emoji, "BUY_PUT", conf,
                             f"Anti-market news {bear}↓/{bull}↑ | {headline_str}")
            return _hold(self.name, self.emoji, f"Mixed macro/political signals {bull}↑/{bear}↓")
        except Exception as e:
            return _hold(self.name, self.emoji, f"Political data unavailable: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# JUDGE AGENT  (fires at 6/9 consensus = 67%)
# ─────────────────────────────────────────────────────────────────────────────
class JudgeAgent:
    name = "Judge Agent"
    emoji = "⚖️"
    THRESHOLD = 5  # out of 9 analysts

    def decide(self, votes: list, ind: dict) -> dict:
        price = ind.get("price", 0)
        atr = ind.get("atr14", price * 0.02) if price else 1

        call_count = sum(1 for v in votes if v["vote"] == "BUY_CALL")
        put_count = sum(1 for v in votes if v["vote"] == "BUY_PUT")
        hold_count = sum(1 for v in votes if v["vote"] == "HOLD")
        total_analysts = len(votes)

        risk = next((v for v in votes if v["agent"] == "Risk Agent"), {})

        if call_count >= self.THRESHOLD:
            signal = "BUY_CALL"
            agreed = [v["agent"] for v in votes if v["vote"] == "BUY_CALL"]
            disagreed = [v["agent"] for v in votes if v["vote"] != "BUY_CALL"]
            conf = float(np.mean([v["confidence"] for v in votes if v["vote"] == "BUY_CALL"]))
            entry = price
            stop = risk.get("stop_loss_long", price - 2 * atr)
            target = risk.get("target_long", price + 3 * atr)
        elif put_count >= self.THRESHOLD:
            signal = "BUY_PUT"
            agreed = [v["agent"] for v in votes if v["vote"] == "BUY_PUT"]
            disagreed = [v["agent"] for v in votes if v["vote"] != "BUY_PUT"]
            conf = float(np.mean([v["confidence"] for v in votes if v["vote"] == "BUY_PUT"]))
            entry = price
            stop = risk.get("stop_loss_short", price + 2 * atr)
            target = risk.get("target_short", price - 3 * atr)
        else:
            signal = "HOLD"
            agreed = []
            disagreed = []
            conf = float(np.mean([v["confidence"] for v in votes])) if votes else 50
            entry = price
            stop = risk.get("stop_loss_long", price - 2 * atr)
            target = price

        direction = "BULLISH" if signal == "BUY_CALL" else "BEARISH" if signal == "BUY_PUT" else "NEUTRAL"
        opts = suggest_options(direction, price, atr, ind)

        # Forecast line (project 7 candles forward)
        forecast = []
        now_ts = int(time.time())
        for i in range(1, 8):
            if signal == "BUY_CALL":
                proj = price + atr * 0.4 * i
                proj = min(proj, target)
            elif signal == "BUY_PUT":
                proj = price - atr * 0.4 * i
                proj = max(proj, target)
            else:
                proj = price
            forecast.append({"time": now_ts + i * 86400, "value": round(proj, 2)})

        vola = ind.get("volatility_20d", 25)
        pos_size = max(1, 5 - int(vola / 15))

        # Get Fear & Greed from cache if available
        fg_score = FearGreedAgent._cache.get("score", 50) if FearGreedAgent._cache else 50
        fg_label = FearGreedAgent._cache.get("label", "Unknown") if FearGreedAgent._cache else "Unknown"

        return {
            "signal": signal,
            "confidence": round(conf, 1),
            "entry_price": round(entry, 2),
            "stop_loss": round(stop, 2),
            "target_price": round(target, 2),
            "agreed_agents": agreed,
            "disagreed_agents": disagreed,
            "vote_tally": {"BUY_CALL": call_count, "BUY_PUT": put_count, "HOLD": hold_count},
            "position_size_pct": pos_size,
            "judge_reason": (
                f"{call_count}/{total_analysts} CALL, {put_count}/{total_analysts} PUT — "
                f"{'✅ CONSENSUS' if signal != 'HOLD' else f'⏳ Need {self.THRESHOLD}/{total_analysts}'}"
            ),
            "action": opts["action"],
            "strike_hint": opts["strike_hint"],
            "expiry_hint": opts["expiry_hint"],
            "entry_trigger": opts["entry_trigger"],
            "risk_note": opts["risk_note"],
            "forecast_line": forecast,
            "fear_greed_score": fg_score,
            "fear_greed_label": fg_label,
        }
