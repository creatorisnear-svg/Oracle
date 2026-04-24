"""
8 Trading Prediction Agents
Each agent analyzes market data from its own perspective and votes BUY, SELL, or HOLD.
"""
import numpy as np
import pandas as pd
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def safe_float(val, default=0.0):
    try:
        f = float(val)
        return f if not np.isnan(f) and not np.isinf(f) else default
    except Exception:
        return default


# ---------------------------------------------------------------------------
# 1. PRICE ACTION AGENT
# ---------------------------------------------------------------------------
class PriceActionAgent:
    name = "Price Action Agent"
    emoji = "📊"

    def analyze(self, df: pd.DataFrame, info: dict) -> dict:
        try:
            closes = df["Close"].values
            opens = df["Open"].values
            highs = df["High"].values
            lows = df["Low"].values
            n = len(closes)
            if n < 10:
                return self._hold("Insufficient data")

            c, o, h, l = closes[-1], opens[-1], highs[-1], lows[-1]
            pc = closes[-2]
            body = abs(c - o)
            candle_range = h - l
            body_pct = body / candle_range if candle_range > 0 else 0
            upper_wick = (h - max(c, o)) / candle_range if candle_range > 0 else 0
            lower_wick = (min(c, o) - l) / candle_range if candle_range > 0 else 0

            signals = []
            # Bullish engulfing
            if c > o and closes[-2] < opens[-2] and c > opens[-2] and o < closes[-2]:
                signals.append(("BUY", 0.8, "Bullish engulfing"))
            # Bearish engulfing
            if c < o and closes[-2] > opens[-2] and c < opens[-2] and o > closes[-2]:
                signals.append(("SELL", 0.8, "Bearish engulfing"))
            # Doji
            if body_pct < 0.1:
                signals.append(("HOLD", 0.6, "Doji – indecision"))
            # Hammer (bullish)
            if lower_wick > 0.6 and upper_wick < 0.1 and c > o:
                signals.append(("BUY", 0.75, "Hammer pattern"))
            # Shooting star (bearish)
            if upper_wick > 0.6 and lower_wick < 0.1 and c < o:
                signals.append(("SELL", 0.75, "Shooting star"))
            # Strong green candle
            if c > o and body_pct > 0.7 and (c - pc) / pc > 0.005:
                signals.append(("BUY", 0.65, "Strong bullish candle"))
            # Strong red candle
            if c < o and body_pct > 0.7 and (pc - c) / pc > 0.005:
                signals.append(("SELL", 0.65, "Strong bearish candle"))
            # Higher highs / higher lows trend
            if n >= 5:
                recent_highs = highs[-5:]
                recent_lows = lows[-5:]
                if recent_highs[-1] > recent_highs[0] and recent_lows[-1] > recent_lows[0]:
                    signals.append(("BUY", 0.6, "Higher highs + higher lows"))
                elif recent_highs[-1] < recent_highs[0] and recent_lows[-1] < recent_lows[0]:
                    signals.append(("SELL", 0.6, "Lower highs + lower lows"))

            return self._aggregate(signals, c, highs, lows)
        except Exception as e:
            return self._hold(f"Error: {e}")

    def _aggregate(self, signals, price, highs, lows):
        if not signals:
            return self._hold("No clear pattern")
        buys = [(s, c, r) for s, c, r in signals if s == "BUY"]
        sells = [(s, c, r) for s, c, r in signals if s == "SELL"]
        if len(buys) > len(sells):
            best = max(buys, key=lambda x: x[1])
            return self._vote("BUY", best[1], best[2])
        elif len(sells) > len(buys):
            best = max(sells, key=lambda x: x[1])
            return self._vote("SELL", best[1], best[2])
        return self._hold("Mixed patterns")

    def _vote(self, direction, confidence, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": direction,
                "confidence": round(confidence * 100, 1), "reason": reason}

    def _hold(self, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": "HOLD",
                "confidence": 50.0, "reason": reason}


# ---------------------------------------------------------------------------
# 2. TECHNICAL AGENT
# ---------------------------------------------------------------------------
class TechnicalAgent:
    name = "Technical Agent"
    emoji = "📈"

    def analyze(self, df: pd.DataFrame, info: dict) -> dict:
        try:
            if len(df) < 26:
                return self._hold("Not enough data for indicators")
            closes = df["Close"]

            # RSI
            delta = closes.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            rsi_now = safe_float(rsi.iloc[-1])

            # MACD
            ema12 = closes.ewm(span=12).mean()
            ema26 = closes.ewm(span=26).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9).mean()
            macd_now = safe_float(macd_line.iloc[-1])
            sig_now = safe_float(signal_line.iloc[-1])
            macd_cross_up = macd_now > sig_now and safe_float(macd_line.iloc[-2]) <= safe_float(signal_line.iloc[-2])
            macd_cross_dn = macd_now < sig_now and safe_float(macd_line.iloc[-2]) >= safe_float(signal_line.iloc[-2])

            # Bollinger Bands
            ma20 = closes.rolling(20).mean()
            std20 = closes.rolling(20).std()
            upper_bb = ma20 + 2 * std20
            lower_bb = ma20 - 2 * std20
            price = safe_float(closes.iloc[-1])
            bb_upper = safe_float(upper_bb.iloc[-1])
            bb_lower = safe_float(lower_bb.iloc[-1])
            bb_mid = safe_float(ma20.iloc[-1])

            # EMA crossover (9/21)
            ema9 = closes.ewm(span=9).mean()
            ema21 = closes.ewm(span=21).mean()
            ema9_now = safe_float(ema9.iloc[-1])
            ema21_now = safe_float(ema21.iloc[-1])
            ema9_prev = safe_float(ema9.iloc[-2])
            ema21_prev = safe_float(ema21.iloc[-2])
            ema_golden = ema9_now > ema21_now and ema9_prev <= ema21_prev
            ema_death = ema9_now < ema21_now and ema9_prev >= ema21_prev

            signals = []
            reasons = []
            # RSI
            if rsi_now < 30:
                signals.append("BUY"); reasons.append(f"RSI oversold ({rsi_now:.1f})")
            elif rsi_now > 70:
                signals.append("SELL"); reasons.append(f"RSI overbought ({rsi_now:.1f})")
            else:
                reasons.append(f"RSI neutral ({rsi_now:.1f})")

            # MACD
            if macd_cross_up:
                signals.append("BUY"); reasons.append("MACD bullish crossover")
            elif macd_cross_dn:
                signals.append("SELL"); reasons.append("MACD bearish crossover")
            elif macd_now > sig_now:
                signals.append("BUY"); reasons.append("MACD above signal")
            else:
                signals.append("SELL"); reasons.append("MACD below signal")

            # Bollinger
            if price < bb_lower:
                signals.append("BUY"); reasons.append("Below lower Bollinger Band")
            elif price > bb_upper:
                signals.append("SELL"); reasons.append("Above upper Bollinger Band")

            # EMA
            if ema_golden:
                signals.append("BUY"); reasons.append("EMA golden cross (9/21)")
            elif ema_death:
                signals.append("SELL"); reasons.append("EMA death cross (9/21)")
            elif ema9_now > ema21_now:
                signals.append("BUY"); reasons.append("Price above EMA trend")
            else:
                signals.append("SELL"); reasons.append("Price below EMA trend")

            buys = signals.count("BUY")
            sells = signals.count("SELL")
            total = buys + sells if buys + sells > 0 else 1
            if buys > sells:
                conf = 55 + (buys / total) * 35
                return self._vote("BUY", conf, " | ".join(reasons[:3]))
            elif sells > buys:
                conf = 55 + (sells / total) * 35
                return self._vote("SELL", conf, " | ".join(reasons[:3]))
            return self._hold(" | ".join(reasons[:2]))
        except Exception as e:
            return self._hold(f"Error: {e}")

    def _vote(self, direction, confidence, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": direction,
                "confidence": round(min(confidence, 95), 1), "reason": reason}

    def _hold(self, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": "HOLD",
                "confidence": 50.0, "reason": reason}


# ---------------------------------------------------------------------------
# 3. VOLUME AGENT
# ---------------------------------------------------------------------------
class VolumeAgent:
    name = "Volume Agent"
    emoji = "📦"

    def analyze(self, df: pd.DataFrame, info: dict) -> dict:
        try:
            if len(df) < 20:
                return self._hold("Not enough data")
            closes = df["Close"].values
            volumes = df["Volume"].values

            avg_vol = np.mean(volumes[-20:])
            curr_vol = safe_float(volumes[-1])
            vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 1

            # OBV
            obv = [0]
            for i in range(1, len(closes)):
                if closes[i] > closes[i - 1]:
                    obv.append(obv[-1] + volumes[i])
                elif closes[i] < closes[i - 1]:
                    obv.append(obv[-1] - volumes[i])
                else:
                    obv.append(obv[-1])
            obv = np.array(obv)
            obv_trend = obv[-1] > np.mean(obv[-10:])  # OBV above recent average

            # Money flow
            highs = df["High"].values
            lows = df["Low"].values
            typical_price = (highs + lows + closes) / 3
            raw_mf = typical_price * volumes
            pos_mf = sum(raw_mf[i] for i in range(1, len(closes)) if closes[i] > closes[i - 1])
            neg_mf = sum(raw_mf[i] for i in range(1, len(closes)) if closes[i] < closes[i - 1])
            mfr = pos_mf / neg_mf if neg_mf > 0 else 1
            mfi = 100 - (100 / (1 + mfr))

            reasons = []
            signals = []

            if vol_ratio > 2.0:
                reasons.append(f"Unusual volume spike ({vol_ratio:.1f}x avg)")
                if closes[-1] > closes[-2]:
                    signals.append("BUY")
                else:
                    signals.append("SELL")
            elif vol_ratio > 1.5:
                reasons.append(f"Above-average volume ({vol_ratio:.1f}x)")

            if obv_trend:
                signals.append("BUY"); reasons.append("OBV trending up")
            else:
                signals.append("SELL"); reasons.append("OBV trending down")

            if mfi > 60:
                signals.append("BUY"); reasons.append(f"Positive money flow (MFI {mfi:.0f})")
            elif mfi < 40:
                signals.append("SELL"); reasons.append(f"Negative money flow (MFI {mfi:.0f})")

            buys = signals.count("BUY")
            sells = signals.count("SELL")
            conf_base = 55 + min(vol_ratio * 5, 20)

            if buys > sells:
                return self._vote("BUY", conf_base, " | ".join(reasons[:3]))
            elif sells > buys:
                return self._vote("SELL", conf_base, " | ".join(reasons[:3]))
            return self._hold(f"Vol {vol_ratio:.1f}x, MFI {mfi:.0f} – neutral")
        except Exception as e:
            return self._hold(f"Error: {e}")

    def _vote(self, direction, confidence, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": direction,
                "confidence": round(min(confidence, 95), 1), "reason": reason}

    def _hold(self, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": "HOLD",
                "confidence": 50.0, "reason": reason}


# ---------------------------------------------------------------------------
# 4. SENTIMENT AGENT
# ---------------------------------------------------------------------------
class SentimentAgent:
    name = "Sentiment Agent"
    emoji = "📰"

    BULLISH_WORDS = [
        "surge", "rally", "gain", "rise", "jump", "soar", "beat", "exceed",
        "record", "high", "growth", "profit", "buy", "upgrade", "bullish",
        "positive", "strong", "boost", "momentum", "breakout", "outperform",
        "dividend", "partnership", "acquisition", "innovation", "revenue",
    ]
    BEARISH_WORDS = [
        "drop", "fall", "crash", "decline", "plunge", "miss", "loss", "sell",
        "downgrade", "bearish", "negative", "weak", "cut", "layoff", "risk",
        "concern", "warn", "volatile", "uncertainty", "lawsuit", "fraud",
        "recall", "shortage", "debt", "deficit",
    ]

    def analyze(self, df: pd.DataFrame, info: dict) -> dict:
        try:
            news = info.get("news", [])
            if not news:
                # Fall back to recent price trend as proxy
                closes = df["Close"].values
                if len(closes) >= 5:
                    pct = (closes[-1] - closes[-5]) / closes[-5] * 100
                    if pct > 2:
                        return self._vote("BUY", 60, f"5-day price trend +{pct:.1f}% (no news data)")
                    elif pct < -2:
                        return self._vote("SELL", 60, f"5-day price trend {pct:.1f}% (no news data)")
                return self._hold("No news data available")

            bull_score = 0
            bear_score = 0
            headlines = []
            for item in news[:10]:
                title = item.get("title", "").lower()
                headlines.append(title)
                for w in self.BULLISH_WORDS:
                    if w in title:
                        bull_score += 1
                for w in self.BEARISH_WORDS:
                    if w in title:
                        bear_score += 1

            total = bull_score + bear_score
            if total == 0:
                return self._hold("Neutral news sentiment")

            sentiment_pct = bull_score / total
            conf = 55 + abs(bull_score - bear_score) / max(total, 1) * 30

            if sentiment_pct > 0.6:
                return self._vote("BUY", conf, f"Bullish news ({bull_score} bull vs {bear_score} bear signals)")
            elif sentiment_pct < 0.4:
                return self._vote("SELL", conf, f"Bearish news ({bear_score} bear vs {bull_score} bull signals)")
            return self._hold(f"Mixed news sentiment ({bull_score}B/{bear_score}S)")
        except Exception as e:
            return self._hold(f"Error: {e}")

    def _vote(self, direction, confidence, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": direction,
                "confidence": round(min(confidence, 90), 1), "reason": reason}

    def _hold(self, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": "HOLD",
                "confidence": 50.0, "reason": reason}


# ---------------------------------------------------------------------------
# 5. OPTIONS FLOW AGENT
# ---------------------------------------------------------------------------
class OptionsFlowAgent:
    name = "Options Flow Agent"
    emoji = "🎯"

    def analyze(self, df: pd.DataFrame, info: dict) -> dict:
        try:
            import yfinance as yf
            symbol = info.get("symbol", "")
            try:
                ticker = yf.Ticker(symbol)
                options_dates = ticker.options
                if not options_dates:
                    return self._hold("No options data available")

                opt_chain = ticker.option_chain(options_dates[0])
                calls = opt_chain.calls
                puts = opt_chain.puts

                if calls.empty and puts.empty:
                    return self._hold("No options chain data")

                total_call_oi = safe_float(calls["openInterest"].sum()) if "openInterest" in calls.columns else 0
                total_put_oi = safe_float(puts["openInterest"].sum()) if "openInterest" in puts.columns else 0
                total_call_vol = safe_float(calls["volume"].sum()) if "volume" in calls.columns else 0
                total_put_vol = safe_float(puts["volume"].sum()) if "volume" in puts.columns else 0

                # Put/Call ratio
                pc_oi = total_put_oi / total_call_oi if total_call_oi > 0 else 1
                pc_vol = total_put_vol / total_call_vol if total_call_vol > 0 else 1

                reasons = [f"P/C OI: {pc_oi:.2f}", f"P/C Vol: {pc_vol:.2f}"]

                # Unusual activity: high call volume relative to put volume
                if pc_vol < 0.6:
                    return self._vote("BUY", 72, f"Bullish options flow: {' | '.join(reasons)}")
                elif pc_vol > 1.4:
                    return self._vote("SELL", 72, f"Bearish options flow: {' | '.join(reasons)}")
                elif pc_oi < 0.7:
                    return self._vote("BUY", 62, f"Call OI dominance: {' | '.join(reasons)}")
                elif pc_oi > 1.3:
                    return self._vote("SELL", 62, f"Put OI dominance: {' | '.join(reasons)}")
                return self._hold(f"Neutral options flow: {' | '.join(reasons)}")
            except Exception:
                return self._hold("Options data unavailable")
        except Exception as e:
            return self._hold(f"Error: {e}")

    def _vote(self, direction, confidence, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": direction,
                "confidence": round(confidence, 1), "reason": reason}

    def _hold(self, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": "HOLD",
                "confidence": 50.0, "reason": reason}


# ---------------------------------------------------------------------------
# 6. MOMENTUM AGENT
# ---------------------------------------------------------------------------
class MomentumAgent:
    name = "Momentum Agent"
    emoji = "⚡"

    def analyze(self, df: pd.DataFrame, info: dict) -> dict:
        try:
            closes = df["Close"].values
            highs = df["High"].values
            lows = df["Low"].values
            n = len(closes)
            if n < 20:
                return self._hold("Not enough data")

            price = closes[-1]
            reasons = []
            signals = []

            # Rate of change (ROC 10)
            roc10 = (closes[-1] - closes[-10]) / closes[-10] * 100 if closes[-10] != 0 else 0
            if roc10 > 3:
                signals.append("BUY"); reasons.append(f"ROC-10: +{roc10:.1f}%")
            elif roc10 < -3:
                signals.append("SELL"); reasons.append(f"ROC-10: {roc10:.1f}%")

            # 20-day high breakout
            high20 = np.max(highs[-20:])
            low20 = np.min(lows[-20:])
            if price >= high20 * 0.99:
                signals.append("BUY"); reasons.append("Near 20-day high breakout")
            elif price <= low20 * 1.01:
                signals.append("SELL"); reasons.append("Near 20-day low breakdown")

            # ADX-like trend strength (simplified)
            tr = np.maximum(highs - lows,
                            np.maximum(np.abs(highs - np.roll(closes, 1)),
                                       np.abs(lows - np.roll(closes, 1))))[1:]
            atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)

            # Consecutive candles
            bull_streak = 0
            bear_streak = 0
            for i in range(n - 1, max(n - 6, 0), -1):
                if closes[i] > closes[i - 1]:
                    bull_streak += 1
                else:
                    break
            for i in range(n - 1, max(n - 6, 0), -1):
                if closes[i] < closes[i - 1]:
                    bear_streak += 1
                else:
                    break
            if bull_streak >= 3:
                signals.append("BUY"); reasons.append(f"{bull_streak}-day bullish streak")
            if bear_streak >= 3:
                signals.append("SELL"); reasons.append(f"{bear_streak}-day bearish streak")

            # Momentum score
            short_mom = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] != 0 else 0
            if short_mom > 1.5:
                signals.append("BUY"); reasons.append(f"Short momentum +{short_mom:.1f}%")
            elif short_mom < -1.5:
                signals.append("SELL"); reasons.append(f"Short momentum {short_mom:.1f}%")

            buys = signals.count("BUY")
            sells = signals.count("SELL")
            total = buys + sells or 1
            conf = 55 + (max(buys, sells) / total) * 30

            if buys > sells:
                return self._vote("BUY", conf, " | ".join(reasons[:3]))
            elif sells > buys:
                return self._vote("SELL", conf, " | ".join(reasons[:3]))
            return self._hold(f"Momentum neutral (ROC: {roc10:.1f}%)")
        except Exception as e:
            return self._hold(f"Error: {e}")

    def _vote(self, direction, confidence, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": direction,
                "confidence": round(min(confidence, 95), 1), "reason": reason}

    def _hold(self, reason):
        return {"agent": self.name, "emoji": self.emoji, "vote": "HOLD",
                "confidence": 50.0, "reason": reason}


# ---------------------------------------------------------------------------
# 7. RISK AGENT
# ---------------------------------------------------------------------------
class RiskAgent:
    name = "Risk Agent"
    emoji = "🛡️"

    def analyze(self, df: pd.DataFrame, info: dict) -> dict:
        try:
            closes = df["Close"].values
            highs = df["High"].values
            lows = df["Low"].values
            price = closes[-1]

            # ATR-based stop loss
            tr = np.maximum(highs[1:] - lows[1:],
                            np.maximum(np.abs(highs[1:] - closes[:-1]),
                                       np.abs(lows[1:] - closes[:-1])))
            atr = np.mean(tr[-14:]) if len(tr) >= 14 else (np.mean(tr) if len(tr) > 0 else price * 0.02)

            stop_loss_long = price - 2 * atr
            stop_loss_short = price + 2 * atr
            target_long = price + 3 * atr
            target_short = price - 3 * atr

            # Volatility regime
            returns = np.diff(closes) / closes[:-1]
            vol = np.std(returns[-20:]) * np.sqrt(252) * 100 if len(returns) >= 20 else 30

            reasons = []
            signals = []

            if vol < 20:
                signals.append("BUY"); reasons.append(f"Low volatility ({vol:.0f}% ann.) – favorable risk")
            elif vol > 50:
                signals.append("SELL"); reasons.append(f"High volatility ({vol:.0f}% ann.) – risk elevated")
            else:
                reasons.append(f"Moderate volatility ({vol:.0f}% ann.)")

            # Risk/reward check based on ATR
            rr_ratio = (target_long - price) / (price - stop_loss_long) if (price - stop_loss_long) > 0 else 1
            if rr_ratio >= 2.5:
                signals.append("BUY"); reasons.append(f"Favorable R/R ratio ({rr_ratio:.1f}x)")
            elif rr_ratio < 1.5:
                signals.append("SELL"); reasons.append(f"Poor R/R ratio ({rr_ratio:.1f}x)")

            # Market cap risk
            mkt_cap = safe_float(info.get("marketCap", 0))
            if mkt_cap > 10e9:
                reasons.append("Large-cap (lower risk)")
            elif mkt_cap > 0:
                reasons.append("Small-cap (higher risk)")

            buys = signals.count("BUY")
            sells = signals.count("SELL")
            conf = 60 + abs(buys - sells) * 10

            vote = "BUY" if buys > sells else "SELL" if sells > buys else "HOLD"
            return {
                "agent": self.name, "emoji": self.emoji, "vote": vote,
                "confidence": round(min(conf, 90), 1),
                "reason": " | ".join(reasons[:3]),
                "stop_loss_long": round(stop_loss_long, 2),
                "stop_loss_short": round(stop_loss_short, 2),
                "target_long": round(target_long, 2),
                "target_short": round(target_short, 2),
                "atr": round(atr, 2),
                "volatility_pct": round(vol, 1),
            }
        except Exception as e:
            return {"agent": self.name, "emoji": self.emoji, "vote": "HOLD",
                    "confidence": 50.0, "reason": f"Error: {e}",
                    "stop_loss_long": 0, "stop_loss_short": 0,
                    "target_long": 0, "target_short": 0, "atr": 0, "volatility_pct": 0}


# ---------------------------------------------------------------------------
# 8. JUDGE AGENT
# ---------------------------------------------------------------------------
class JudgeAgent:
    name = "Judge Agent"
    emoji = "⚖️"
    AGREEMENT_THRESHOLD = 6

    def decide(self, votes: list[dict], price: float, risk_data: dict) -> dict:
        buy_count = sum(1 for v in votes if v["vote"] == "BUY")
        sell_count = sum(1 for v in votes if v["vote"] == "SELL")
        hold_count = sum(1 for v in votes if v["vote"] == "HOLD")
        total = len(votes)

        agreed_buy = [v for v in votes if v["vote"] == "BUY"]
        agreed_sell = [v for v in votes if v["vote"] == "SELL"]
        disagreed = [v for v in votes if v["vote"] == "HOLD"]

        avg_confidence = np.mean([v.get("confidence", 50) for v in votes])

        if buy_count >= self.AGREEMENT_THRESHOLD:
            signal = "BUY"
            agreed = agreed_buy
            conf = np.mean([v["confidence"] for v in agreed_buy])
            entry = price
            stop = risk_data.get("stop_loss_long", price * 0.95)
            target = risk_data.get("target_long", price * 1.08)
            disagreed_agents = [v["agent"] for v in votes if v["vote"] != "BUY"]
            agreed_agents = [v["agent"] for v in votes if v["vote"] == "BUY"]
        elif sell_count >= self.AGREEMENT_THRESHOLD:
            signal = "SELL"
            agreed = agreed_sell
            conf = np.mean([v["confidence"] for v in agreed_sell])
            entry = price
            stop = risk_data.get("stop_loss_short", price * 1.05)
            target = risk_data.get("target_short", price * 0.92)
            disagreed_agents = [v["agent"] for v in votes if v["vote"] != "SELL"]
            agreed_agents = [v["agent"] for v in votes if v["vote"] == "SELL"]
        else:
            signal = "HOLD"
            conf = avg_confidence
            entry = price
            stop = risk_data.get("stop_loss_long", price * 0.95)
            target = price
            disagreed_agents = []
            agreed_agents = []

        position_size_pct = 5 if signal != "HOLD" else 0
        if risk_data.get("volatility_pct", 30) > 40:
            position_size_pct = max(position_size_pct - 2, 1)

        return {
            "signal": signal,
            "confidence": round(float(conf), 1),
            "entry_price": round(float(entry), 2),
            "stop_loss": round(float(stop), 2),
            "target_price": round(float(target), 2),
            "agreed_agents": agreed_agents,
            "disagreed_agents": disagreed_agents,
            "vote_tally": {"BUY": buy_count, "SELL": sell_count, "HOLD": hold_count},
            "position_size_pct": position_size_pct,
            "judge_reason": (
                f"{buy_count}/8 agents voted BUY, {sell_count}/8 SELL — "
                f"{'Consensus reached' if signal != 'HOLD' else 'No consensus (need 6/8)'}"
            ),
        }
