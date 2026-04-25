"""
8 Trading Prediction Agents — Full Hop indicator suite
Output: BUY_CALL | BUY_PUT | HOLD
Each agent is specialized; Judge fires only at 6/8 consensus.
"""
import numpy as np
import pandas as pd
from indicators import (
    compute_all_indicators, score_indicators_to_direction,
    suggest_options, safe_float, compute_supertrend,
    compute_stochastic, compute_rsi, compute_vwap,
)

Signal = str  # "BUY_CALL" | "BUY_PUT" | "HOLD"


def _vote(agent_name: str, emoji: str, signal: Signal, confidence: float, reason: str, **extra) -> dict:
    return {"agent": agent_name, "emoji": emoji, "vote": signal,
            "confidence": round(min(confidence, 97), 1), "reason": reason, **extra}


def _hold(agent_name: str, emoji: str, reason: str) -> dict:
    return {"agent": agent_name, "emoji": emoji, "vote": "HOLD",
            "confidence": 50.0, "reason": reason}


# ─────────────────────────────────────────────────────────────────────────────
# 1. PRICE ACTION AGENT
# ─────────────────────────────────────────────────────────────────────────────
class PriceActionAgent:
    name = "Price Action Agent"
    emoji = "🕯️"

    def analyze(self, df: pd.DataFrame, ind: dict) -> dict:
        try:
            closes = df["Close"].values
            opens = df["Open"].values
            highs = df["High"].values
            lows = df["Low"].values
            n = len(closes)
            if n < 10:
                return _hold(self.name, self.emoji, "Insufficient candle data")

            c, o, h, l = closes[-1], opens[-1], highs[-1], lows[-1]
            pc, po = closes[-2], opens[-2]
            body = abs(c - o)
            rng = h - l
            bp = body / rng if rng > 0 else 0
            uw = (h - max(c, o)) / rng if rng > 0 else 0
            lw = (min(c, o) - l) / rng if rng > 0 else 0

            signals, reasons = [], []

            # Bullish engulfing → CALL
            if c > o and pc < po and c > po and o < pc:
                signals.append("BUY_CALL"); reasons.append("Bullish engulfing pattern")
            # Bearish engulfing → PUT
            if c < o and pc > po and c < po and o > pc:
                signals.append("BUY_PUT"); reasons.append("Bearish engulfing pattern")
            # Hammer → CALL
            if lw > 0.6 and uw < 0.1 and c >= o:
                signals.append("BUY_CALL"); reasons.append("Hammer (bullish reversal)")
            # Shooting star → PUT
            if uw > 0.6 and lw < 0.1 and c <= o:
                signals.append("BUY_PUT"); reasons.append("Shooting star (bearish reversal)")
            # Doji
            if bp < 0.1:
                reasons.append("Doji — indecision")
            # Strong momentum candle
            if c > o and bp > 0.65 and ind.get("rel_volume", 1) > 1.3:
                signals.append("BUY_CALL"); reasons.append(f"Strong bullish candle + {ind.get('rel_volume',1):.1f}× vol")
            if c < o and bp > 0.65 and ind.get("rel_volume", 1) > 1.3:
                signals.append("BUY_PUT"); reasons.append(f"Strong bearish candle + {ind.get('rel_volume',1):.1f}× vol")
            # Higher highs / higher lows (bullish structure)
            if n >= 5:
                if highs[-1] > highs[-5] and lows[-1] > lows[-5]:
                    signals.append("BUY_CALL"); reasons.append("Higher highs + higher lows structure")
                elif highs[-1] < highs[-5] and lows[-1] < lows[-5]:
                    signals.append("BUY_PUT"); reasons.append("Lower highs + lower lows structure")
            # SuperTrend agreement
            if ind.get("supertrend_dir") == "up":
                signals.append("BUY_CALL"); reasons.append(f"SuperTrend bullish (dist {ind.get('supertrend_dist_pct',0):.1f}%)")
            else:
                signals.append("BUY_PUT"); reasons.append(f"SuperTrend bearish")

            calls = signals.count("BUY_CALL")
            puts = signals.count("BUY_PUT")
            conf = 55 + (max(calls, puts) / max(calls + puts, 1)) * 30
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

    def analyze(self, df: pd.DataFrame, ind: dict) -> dict:
        try:
            result = score_indicators_to_direction(ind)
            direction = result["direction"]
            conf = result["confidence"]
            score = result["score"]

            rsi = ind.get("rsi14", 50)
            stoch = ind.get("stoch_k", 50)
            st_dir = ind.get("supertrend_dir", "down")
            macd_h = ind.get("macd_hist", 0)

            reasons = [
                f"Composite score {score:+.1f}/9",
                f"RSI {rsi:.0f} | Stoch {stoch:.0f}",
                f"SuperTrend {st_dir} | MACD hist {macd_h:+.4f}",
            ]

            if direction == "BULLISH":
                return _vote(self.name, self.emoji, "BUY_CALL", conf, " | ".join(reasons))
            elif direction == "BEARISH":
                return _vote(self.name, self.emoji, "BUY_PUT", conf, " | ".join(reasons))
            return _hold(self.name, self.emoji, f"Neutral — score {score:+.1f} | " + " | ".join(reasons[:2]))
        except Exception as e:
            return _hold(self.name, self.emoji, f"Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. VOLUME AGENT
# ─────────────────────────────────────────────────────────────────────────────
class VolumeAgent:
    name = "Volume Agent"
    emoji = "📦"

    def analyze(self, df: pd.DataFrame, ind: dict) -> dict:
        try:
            rel_v = ind.get("rel_volume", 1.0)
            obv_slope = ind.get("obv_slope_10d_pct", 0.0)
            up_dn = ind.get("up_dn_vol_ratio", 1.0)
            vol_trend = ind.get("vol_trend_5v20", 1.0)
            vc = ind.get("vol_confirms", "neutral")
            ch = ind.get("change_1d", 0)

            signals, reasons = [], []

            # Unusual volume spike
            if rel_v > 2.0:
                reasons.append(f"Unusual volume spike {rel_v:.1f}× avg")
                signals.append("BUY_CALL" if ch > 0 else "BUY_PUT")
            elif rel_v > 1.5:
                reasons.append(f"Above-avg volume {rel_v:.1f}×")
                signals.append("BUY_CALL" if ch > 0 else "BUY_PUT")

            # OBV slope (dark pool proxy)
            if obv_slope > 8:
                signals.append("BUY_CALL"); reasons.append(f"OBV rising +{obv_slope:.0f}% (10d)")
            elif obv_slope < -8:
                signals.append("BUY_PUT"); reasons.append(f"OBV falling {obv_slope:.0f}% (10d)")

            # Up/down day volume ratio (accumulation vs distribution)
            if up_dn > 1.4:
                signals.append("BUY_CALL"); reasons.append(f"Accumulation: up/dn vol {up_dn:.2f}x")
            elif up_dn < 0.7:
                signals.append("BUY_PUT"); reasons.append(f"Distribution: up/dn vol {up_dn:.2f}x")

            # Volume trend (building vs drying)
            if vol_trend > 1.3:
                reasons.append(f"Vol building (5d/20d {vol_trend:.1f}×)")
                signals.append("BUY_CALL" if ch > 0 else "BUY_PUT")
            elif vol_trend < 0.7:
                reasons.append("Volume drying up — low conviction")

            # Volume confirms move
            if vc == "confirms" and ch > 0:
                signals.append("BUY_CALL"); reasons.append("Volume confirms bullish move")
            elif vc == "confirms" and ch < 0:
                signals.append("BUY_PUT"); reasons.append("Volume confirms bearish move")

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

    BULLISH = ["surge","rally","gain","rise","jump","soar","beat","exceed","record","high","growth",
               "profit","upgrade","bullish","positive","strong","boost","breakout","outperform",
               "dividend","partnership","acquisition","innovation","revenue","approval","launch",
               "deal","contract","buy","raises","guidance","beat","recovery","expansion"]
    BEARISH = ["drop","fall","crash","decline","plunge","miss","loss","sell","downgrade","bearish",
               "negative","weak","cut","layoff","risk","concern","warn","volatile","uncertainty",
               "lawsuit","fraud","recall","shortage","debt","deficit","investigation","probe",
               "default","bankruptcy","missed","disappoints","suspended","warning","downside"]

    def analyze(self, df: pd.DataFrame, ind: dict) -> dict:
        try:
            news = ind.get("_news", [])
            if not news:
                # Use 5-day price trend as proxy
                ch5 = ind.get("change_5d", 0)
                if ch5 > 3:
                    return _vote(self.name, self.emoji, "BUY_CALL", 62, f"5-day trend +{ch5:.1f}% (no live news)")
                elif ch5 < -3:
                    return _vote(self.name, self.emoji, "BUY_PUT", 62, f"5-day trend {ch5:.1f}% (no live news)")
                return _hold(self.name, self.emoji, "No news data; price trend neutral")

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
                return _vote(self.name, self.emoji, "BUY_CALL", conf,
                             f"Bullish news: {bull_score}↑ vs {bear_score}↓ signals")
            elif ratio < 0.4:
                return _vote(self.name, self.emoji, "BUY_PUT", conf,
                             f"Bearish news: {bear_score}↓ vs {bull_score}↑ signals")
            return _hold(self.name, self.emoji, f"Mixed: {bull_score}↑ / {bear_score}↓")
        except Exception as e:
            return _hold(self.name, self.emoji, f"Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. OPTIONS FLOW AGENT
# ─────────────────────────────────────────────────────────────────────────────
class OptionsFlowAgent:
    name = "Options Flow Agent"
    emoji = "🎯"

    def analyze(self, df: pd.DataFrame, ind: dict) -> dict:
        try:
            import yfinance as yf
            symbol = ind.get("_symbol", "")
            if not symbol:
                return _hold(self.name, self.emoji, "No symbol provided")

            ticker = yf.Ticker(symbol)
            dates = ticker.options
            if not dates:
                return _hold(self.name, self.emoji, "No options chain available")

            # Check near-term and next expiry
            chain0 = ticker.option_chain(dates[0])
            calls = chain0.calls
            puts = chain0.puts

            total_call_oi = safe_float(calls["openInterest"].sum()) if "openInterest" in calls.columns else 0
            total_put_oi = safe_float(puts["openInterest"].sum()) if "openInterest" in puts.columns else 0
            total_call_vol = safe_float(calls["volume"].sum()) if "volume" in calls.columns else 0
            total_put_vol = safe_float(puts["volume"].sum()) if "volume" in puts.columns else 0

            pc_oi = total_put_oi / total_call_oi if total_call_oi > 0 else 1
            pc_vol = total_put_vol / total_call_vol if total_call_vol > 0 else 1

            # Unusual call activity (big bets on upside)
            reasons = [f"P/C OI: {pc_oi:.2f}", f"P/C Vol: {pc_vol:.2f}"]

            if pc_vol < 0.5:
                return _vote(self.name, self.emoji, "BUY_CALL", 80,
                             f"Heavy call flow — {' | '.join(reasons)}")
            elif pc_vol < 0.7:
                return _vote(self.name, self.emoji, "BUY_CALL", 68,
                             f"Call-dominated flow — {' | '.join(reasons)}")
            elif pc_vol > 2.0:
                return _vote(self.name, self.emoji, "BUY_PUT", 80,
                             f"Heavy put flow — {' | '.join(reasons)}")
            elif pc_vol > 1.4:
                return _vote(self.name, self.emoji, "BUY_PUT", 68,
                             f"Put-dominated flow — {' | '.join(reasons)}")
            elif pc_oi < 0.7:
                return _vote(self.name, self.emoji, "BUY_CALL", 62,
                             f"Call OI dominance — {' | '.join(reasons)}")
            elif pc_oi > 1.3:
                return _vote(self.name, self.emoji, "BUY_PUT", 62,
                             f"Put OI dominance — {' | '.join(reasons)}")
            return _hold(self.name, self.emoji, f"Neutral flow — {' | '.join(reasons)}")
        except Exception as e:
            return _hold(self.name, self.emoji, f"Options unavailable: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. MOMENTUM AGENT (short-term focus, intraday awareness)
# ─────────────────────────────────────────────────────────────────────────────
class MomentumAgent:
    name = "Momentum Agent"
    emoji = "⚡"

    def analyze(self, df: pd.DataFrame, ind: dict) -> dict:
        try:
            closes = df["Close"].values
            highs = df["High"].values
            lows = df["Low"].values
            n = len(closes)
            if n < 10:
                return _hold(self.name, self.emoji, "Not enough data")

            price = closes[-1]
            signals, reasons = [], []

            # Rate of change
            roc5 = (closes[-1] - closes[-5]) / closes[-5] * 100 if n >= 5 and closes[-5] > 0 else 0
            roc10 = (closes[-1] - closes[-10]) / closes[-10] * 100 if n >= 10 and closes[-10] > 0 else 0

            if roc5 > 2:
                signals.append("BUY_CALL"); reasons.append(f"5D ROC +{roc5:.1f}%")
            elif roc5 < -2:
                signals.append("BUY_PUT"); reasons.append(f"5D ROC {roc5:.1f}%")
            if roc10 > 4:
                signals.append("BUY_CALL"); reasons.append(f"10D ROC +{roc10:.1f}%")
            elif roc10 < -4:
                signals.append("BUY_PUT"); reasons.append(f"10D ROC {roc10:.1f}%")

            # 20-day high/low breakout
            h20 = np.max(highs[-20:]) if n >= 20 else np.max(highs)
            l20 = np.min(lows[-20:]) if n >= 20 else np.min(lows)
            if price >= h20 * 0.99:
                signals.append("BUY_CALL"); reasons.append(f"Near 20D breakout high ${h20:.2f}")
            elif price <= l20 * 1.01:
                signals.append("BUY_PUT"); reasons.append(f"Near 20D breakdown low ${l20:.2f}")

            # Streak
            bull_streak = 0
            for i in range(n-1, max(n-7, 0), -1):
                if closes[i] > closes[i-1]: bull_streak += 1
                else: break
            bear_streak = 0
            for i in range(n-1, max(n-7, 0), -1):
                if closes[i] < closes[i-1]: bear_streak += 1
                else: break

            if bull_streak >= 3:
                signals.append("BUY_CALL"); reasons.append(f"{bull_streak}-day bull streak")
            if bear_streak >= 3:
                signals.append("BUY_PUT"); reasons.append(f"{bear_streak}-day bear streak")

            # VWAP momentum
            pvwap = ind.get("price_vs_vwap_pct", 0)
            if pvwap > 1.0:
                signals.append("BUY_CALL"); reasons.append(f"Price {pvwap:.1f}% above VWAP")
            elif pvwap < -1.0:
                signals.append("BUY_PUT"); reasons.append(f"Price {pvwap:.1f}% below VWAP")

            calls = signals.count("BUY_CALL")
            puts = signals.count("BUY_PUT")
            total = calls + puts or 1
            conf = 55 + (max(calls, puts) / total) * 32
            r = " | ".join(reasons[:3]) or f"ROC5: {roc5:+.1f}%"

            if calls > puts:
                return _vote(self.name, self.emoji, "BUY_CALL", conf, r)
            elif puts > calls:
                return _vote(self.name, self.emoji, "BUY_PUT", conf, r)
            return _hold(self.name, self.emoji, r)
        except Exception as e:
            return _hold(self.name, self.emoji, f"Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 7. RISK AGENT
# ─────────────────────────────────────────────────────────────────────────────
class RiskAgent:
    name = "Risk Agent"
    emoji = "🛡️"

    def analyze(self, df: pd.DataFrame, ind: dict) -> dict:
        try:
            price = ind.get("price", df["Close"].iloc[-1])
            atr = ind.get("atr14", price * 0.02)
            vola = ind.get("volatility_20d", 25)
            rr = 3 / 2  # ATR-based risk/reward is 3:2 target:stop

            signals, reasons = [], []

            # Volatility regime
            if vola < 20:
                signals.append("BUY_CALL"); reasons.append(f"Low vol {vola:.0f}% — options cheap, good risk")
            elif vola > 50:
                signals.append("BUY_PUT"); reasons.append(f"High vol {vola:.0f}% — use spreads; premium elevated")
            else:
                reasons.append(f"Moderate vol {vola:.0f}%")

            # ATR-based risk assessment
            atr_pct = atr / price * 100 if price > 0 else 2
            if atr_pct < 1.5:
                signals.append("BUY_CALL"); reasons.append(f"Tight ATR {atr_pct:.1f}% — defined risk on calls")
            elif atr_pct > 4:
                signals.append("BUY_PUT"); reasons.append(f"Wide ATR {atr_pct:.1f}% — high daily swings")

            # 52-week position
            h52 = ind.get("high_52w", 0)
            l52 = ind.get("low_52w", 0)
            if h52 > 0 and l52 > 0:
                pos_52w = (price - l52) / (h52 - l52) * 100
                if pos_52w > 80:
                    reasons.append(f"Near 52W high ({pos_52w:.0f}%) — extended")
                elif pos_52w < 20:
                    reasons.append(f"Near 52W low ({pos_52w:.0f}%) — oversold")

            calls = signals.count("BUY_CALL")
            puts = signals.count("BUY_PUT")
            conf = 60 + abs(calls - puts) * 8
            stop_long = round(price - 2 * atr, 2)
            stop_short = round(price + 2 * atr, 2)
            tgt_long = round(price + 3 * atr, 2)
            tgt_short = round(price - 3 * atr, 2)

            base = {
                "stop_loss_long": stop_long, "stop_loss_short": stop_short,
                "target_long": tgt_long, "target_short": tgt_short,
                "atr": round(atr, 3), "volatility_pct": round(vola, 1),
            }
            r = " | ".join(reasons[:3])
            if calls > puts:
                return {**_vote(self.name, self.emoji, "BUY_CALL", conf, r), **base}
            elif puts > calls:
                return {**_vote(self.name, self.emoji, "BUY_PUT", conf, r), **base}
            return {**_hold(self.name, self.emoji, r), **base}
        except Exception as e:
            return {**_hold(self.name, self.emoji, f"Error: {e}"),
                    "stop_loss_long": 0, "stop_loss_short": 0,
                    "target_long": 0, "target_short": 0,
                    "atr": 0, "volatility_pct": 25}


# ─────────────────────────────────────────────────────────────────────────────
# 8. JUDGE AGENT
# ─────────────────────────────────────────────────────────────────────────────
class JudgeAgent:
    name = "Judge Agent"
    emoji = "⚖️"
    THRESHOLD = 6  # Need 6 of 8 (7 analysts + judge = 8 total logic path)

    def decide(self, votes: list[dict], ind: dict) -> dict:
        price = ind.get("price", 0)
        atr = ind.get("atr14", price * 0.02) if price else 1

        call_count = sum(1 for v in votes if v["vote"] == "BUY_CALL")
        put_count = sum(1 for v in votes if v["vote"] == "BUY_PUT")
        hold_count = sum(1 for v in votes if v["vote"] == "HOLD")

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
            conf = float(np.mean([v["confidence"] for v in votes]))
            entry = price
            stop = risk.get("stop_loss_long", price - 2 * atr)
            target = price

        # Options recommendations
        direction = "BULLISH" if signal == "BUY_CALL" else "BEARISH" if signal == "BUY_PUT" else "NEUTRAL"
        opts = suggest_options(direction, price, atr, ind)

        # Forecast candles (project N daily candles forward for chart)
        forecast = []
        import time
        now_ts = int(time.time())
        interval = 86400  # 1 day
        proj_price = price
        for i in range(1, 8):
            if signal == "BUY_CALL":
                step = atr * 0.4  # daily expected move toward target
                proj_price = min(proj_price + step, target)
            elif signal == "BUY_PUT":
                step = atr * 0.4
                proj_price = max(proj_price - step, target)
            forecast.append({"time": now_ts + i * interval, "value": round(proj_price, 2)})

        vola = ind.get("volatility_pct", 25) if isinstance(ind, dict) else 25
        pos_size = max(1, 5 - int(vola / 15))  # smaller size in high vol

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
                f"{call_count}/7 CALL, {put_count}/7 PUT — "
                f"{'✅ CONSENSUS REACHED' if signal != 'HOLD' else '⏳ No consensus (need 6/7)'}"
            ),
            "forecast_line": forecast,
            # Options details from Hop
            "action": opts["action"],
            "strike_hint": opts["strike_hint"],
            "expiry_hint": opts["expiry_hint"],
            "entry_trigger": opts["entry_trigger"],
            "risk_note": opts["risk_note"],
        }
