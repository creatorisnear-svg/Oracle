"""
Technical Indicators Library
Drawn from original Hop market.ts indicator set.
Includes: SMA, EMA, RSI, MACD, Bollinger, SuperTrend, VWAP, Stochastic, OBV, ATR, Multi-TF Trend
"""
import numpy as np
import pandas as pd
from typing import Optional


def safe_float(val, default=0.0):
    try:
        f = float(val)
        return f if not (np.isnan(f) or np.isinf(f)) else default
    except Exception:
        return default


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = np.zeros(n)
    atr[period-1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i-1] * (period-1) + tr[i]) / period
    return atr


def compute_supertrend(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 10, multiplier: float = 3.0):
    """SuperTrend indicator — classic trend-following overlay from Hop."""
    atr = compute_atr(highs, lows, closes, period)
    n = len(closes)
    hl2 = (highs + lows) / 2
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = np.copy(basic_upper)
    final_lower = np.copy(basic_lower)
    direction = np.ones(n)  # 1 = bullish, -1 = bearish
    supertrend = np.zeros(n)

    for i in range(1, n):
        if basic_upper[i] < final_upper[i-1] or closes[i-1] > final_upper[i-1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i-1]

        if basic_lower[i] > final_lower[i-1] or closes[i-1] < final_lower[i-1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i-1]

        if direction[i-1] == -1 and closes[i] > final_upper[i]:
            direction[i] = 1
        elif direction[i-1] == 1 and closes[i] < final_lower[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]

        supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return supertrend, direction


def compute_vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    typical = (highs + lows + closes) / 3
    cum_tp_vol = np.cumsum(typical * volumes)
    cum_vol = np.cumsum(volumes)
    with np.errstate(divide='ignore', invalid='ignore'):
        vwap = np.where(cum_vol > 0, cum_tp_vol / cum_vol, closes)
    return vwap


def compute_stochastic(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    stoch = np.full(len(closes), 50.0)
    for i in range(period-1, len(closes)):
        low_min = np.min(lows[i-period+1:i+1])
        high_max = np.max(highs[i-period+1:i+1])
        rng = high_max - low_min
        stoch[i] = ((closes[i] - low_min) / rng * 100) if rng > 0 else 50.0
    return stoch


def compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(closes, prepend=closes[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.zeros(len(closes))
    avg_loss = np.zeros(len(closes))
    avg_gain[period] = np.mean(gain[1:period+1])
    avg_loss[period] = np.mean(loss[1:period+1])
    for i in range(period+1, len(closes)):
        avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i-1] * (period-1) + loss[i]) / period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    return 100 - (100 / (1 + rs))


def compute_macd(closes: np.ndarray, fast=12, slow=26, signal=9):
    s = pd.Series(closes)
    ema_fast = s.ewm(span=fast, adjust=False).mean().values
    ema_slow = s.ewm(span=slow, adjust=False).mean().values
    macd_line = ema_fast - ema_slow
    sig_line = pd.Series(macd_line).ewm(span=signal, adjust=False).mean().values
    hist = macd_line - sig_line
    return macd_line, sig_line, hist


def compute_bollinger(closes: np.ndarray, period: int = 20, std_mult: float = 2.0):
    s = pd.Series(closes)
    mid = s.rolling(period).mean().values
    std = s.rolling(period).std().values
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width_pct = np.where(mid > 0, (upper - lower) / mid * 100, 0.0)
    return upper, mid, lower, width_pct


def compute_obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    obv = np.zeros(len(closes))
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv[i] = obv[i-1] + volumes[i]
        elif closes[i] < closes[i-1]:
            obv[i] = obv[i-1] - volumes[i]
        else:
            obv[i] = obv[i-1]
    return obv


def multi_tf_trend_score(closes: np.ndarray) -> float:
    """
    Score from -3 to +3 based on price position relative to SMA20/50.
    From original Hop market.ts trendScore logic.
    """
    if len(closes) < 50:
        return 0.0
    s = pd.Series(closes)
    sma20 = s.rolling(20).mean().iloc[-1]
    sma50 = s.rolling(50).mean().iloc[-1]
    price = closes[-1]
    score = 0.0
    if safe_float(sma20) > 0:
        score += 1 if price > sma20 else -1
    if safe_float(sma50) > 0:
        score += 1 if price > sma50 else -1
    if safe_float(sma20) > 0 and safe_float(sma50) > 0:
        score += 1 if sma20 > sma50 else -1
    return score


def compute_all_indicators(df: pd.DataFrame) -> dict:
    """Compute the full indicator set used across all agents."""
    closes = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    volumes = df["Volume"].values.astype(float)
    n = len(closes)

    atr = compute_atr(highs, lows, closes)
    supertrend_vals, st_dir = compute_supertrend(highs, lows, closes)
    vwap = compute_vwap(highs, lows, closes, volumes)
    stoch = compute_stochastic(highs, lows, closes)
    rsi = compute_rsi(closes)
    macd_line, macd_sig, macd_hist = compute_macd(closes)
    bb_upper, bb_mid, bb_lower, bb_width = compute_bollinger(closes)
    obv = compute_obv(closes, volumes)
    trend_score = multi_tf_trend_score(closes)

    # Volume stats
    avg_vol_20 = np.mean(volumes[-20:]) if n >= 20 else np.mean(volumes)
    rel_vol = volumes[-1] / avg_vol_20 if avg_vol_20 > 0 else 1.0
    vol_trend_5v20 = (np.mean(volumes[-5:]) / avg_vol_20) if n >= 20 else 1.0

    # Up/down day volume ratio (last 20d)
    up_vol = sum(volumes[i] for i in range(max(n-20, 1), n) if closes[i] > closes[i-1])
    dn_vol = sum(volumes[i] for i in range(max(n-20, 1), n) if closes[i] < closes[i-1])
    up_dn_ratio = up_vol / dn_vol if dn_vol > 0 else 1.0

    # OBV slope (10d %)
    if n >= 10:
        obv_start = obv[-10]
        obv_slope = (obv[-1] - obv_start) / (abs(obv_start) + 1e-9) * 100
    else:
        obv_slope = 0.0

    price = closes[-1]
    prev_close = closes[-2] if n >= 2 else closes[-1]
    change_1d = (price - prev_close) / prev_close * 100 if prev_close else 0
    change_5d = (price - closes[-5]) / closes[-5] * 100 if n >= 5 else 0

    # SuperTrend stats
    st_now = st_dir[-1]
    st_flips = 0
    for i in range(n-2, max(n-20, 0), -1):
        if st_dir[i] != st_dir[i+1]:
            st_flips = n - 1 - i
            break

    # Price vs VWAP
    price_vs_vwap = (price - vwap[-1]) / vwap[-1] * 100 if vwap[-1] > 0 else 0

    # Volume confirms move
    if volumes[-1] > avg_vol_20 * 1.2 and change_1d > 0:
        vol_confirm = "confirms"
    elif volumes[-1] > avg_vol_20 * 1.2 and change_1d < 0:
        vol_confirm = "confirms"
    elif volumes[-1] < avg_vol_20 * 0.8:
        vol_confirm = "diverges"
    else:
        vol_confirm = "neutral"

    return {
        "price": price,
        "prev_close": prev_close,
        "change_1d": change_1d,
        "change_5d": change_5d,
        # Moving averages
        "sma20": safe_float(pd.Series(closes).rolling(20).mean().iloc[-1]),
        "sma50": safe_float(pd.Series(closes).rolling(50).mean().iloc[-1]),
        "ema9": safe_float(pd.Series(closes).ewm(span=9).mean().iloc[-1]),
        "ema21": safe_float(pd.Series(closes).ewm(span=21).mean().iloc[-1]),
        # RSI
        "rsi14": safe_float(rsi[-1]),
        "rsi_series": rsi,
        # MACD
        "macd": safe_float(macd_line[-1]),
        "macd_signal": safe_float(macd_sig[-1]),
        "macd_hist": safe_float(macd_hist[-1]),
        "macd_hist_prev": safe_float(macd_hist[-2]) if n >= 2 else 0,
        "macd_cross_up": macd_line[-1] > macd_sig[-1] and macd_line[-2] <= macd_sig[-2] if n >= 2 else False,
        "macd_cross_dn": macd_line[-1] < macd_sig[-1] and macd_line[-2] >= macd_sig[-2] if n >= 2 else False,
        # Bollinger
        "bb_upper": safe_float(bb_upper[-1]),
        "bb_mid": safe_float(bb_mid[-1]),
        "bb_lower": safe_float(bb_lower[-1]),
        "bb_width_pct": safe_float(bb_width[-1]),
        # Stochastic
        "stoch_k": safe_float(stoch[-1]),
        # ATR
        "atr14": safe_float(atr[-1]),
        "atr_pct": safe_float(atr[-1] / price * 100) if price else 0,
        # SuperTrend
        "supertrend": safe_float(supertrend_vals[-1]),
        "supertrend_dir": "up" if st_now > 0 else "down",
        "supertrend_dist_pct": safe_float((price - supertrend_vals[-1]) / price * 100) if price else 0,
        "supertrend_flip_bars_ago": st_flips,
        # VWAP
        "vwap": safe_float(vwap[-1]),
        "price_vs_vwap_pct": safe_float(price_vs_vwap),
        # Volume
        "rel_volume": safe_float(rel_vol),
        "avg_volume_20d": safe_float(avg_vol_20),
        "vol_trend_5v20": safe_float(vol_trend_5v20),
        "up_dn_vol_ratio": safe_float(up_dn_ratio),
        "obv_slope_10d_pct": safe_float(obv_slope),
        "vol_confirms": vol_confirm,
        # Multi-TF trend
        "trend_score": safe_float(trend_score),
        # 52w
        "high_52w": safe_float(np.max(highs)),
        "low_52w": safe_float(np.min(lows)),
        # Volatility
        "volatility_20d": safe_float(np.std(np.diff(closes[-21:]) / closes[-21:-1]) * np.sqrt(252) * 100) if n >= 21 else 20.0,
    }


def score_indicators_to_direction(ind: dict) -> dict:
    """
    Convert indicator dict → directional score.
    Adapted from Hop marketBacktest.ts scoreIndicators().
    Returns score (-6..+6), confidence, direction.
    """
    score = 0.0

    # 1. Multi-TF trend (-3..+3)
    score += ind.get("trend_score", 0)

    # 2. RSI (tiered, from Hop)
    rsi = ind.get("rsi14", 50)
    if rsi >= 70:   score += 2.5
    elif rsi >= 60: score += 1.5
    elif rsi >= 55: score += 0.5
    elif rsi <= 30: score -= 2.5
    elif rsi <= 40: score -= 1.5
    elif rsi <= 45: score -= 0.5

    # 3. MACD
    if ind.get("macd_cross_up"): score += 2.0
    elif ind.get("macd_cross_dn"): score -= 2.0
    elif ind.get("macd_hist", 0) > 0: score += 0.5
    else: score -= 0.5

    # 4. Bollinger
    price = ind.get("price", 0)
    if price > 0:
        bb_u = ind.get("bb_upper", price)
        bb_l = ind.get("bb_lower", price)
        bb_m = ind.get("bb_mid", price)
        if price >= bb_u: score += 1.5
        elif price >= bb_m + (bb_u - bb_m) * 0.8: score += 0.5
        elif price <= bb_l: score -= 1.5
        elif price <= bb_m - (bb_m - bb_l) * 0.8: score -= 0.5

    # 5. SuperTrend
    if ind.get("supertrend_dir") == "up": score += 1.5
    else: score -= 1.5

    # 6. VWAP bias
    pvwap = ind.get("price_vs_vwap_pct", 0)
    if pvwap > 0.5: score += 0.5
    elif pvwap < -0.5: score -= 0.5

    # 7. Stochastic
    stoch = ind.get("stoch_k", 50)
    if stoch >= 80: score += 1.0
    elif stoch >= 70: score += 0.5
    elif stoch <= 20: score -= 1.0
    elif stoch <= 30: score -= 0.5

    # 8. Volume confirmation
    rel_v = ind.get("rel_volume", 1.0)
    ch = ind.get("change_1d", 0)
    if rel_v > 1.5:
        score += 0.5 if ch > 0 else -0.5
    vc = ind.get("vol_confirms", "neutral")
    if vc == "confirms":
        score += 0.3 if ch > 0 else -0.3

    # 9. OBV slope
    obv_s = ind.get("obv_slope_10d_pct", 0)
    if obv_s > 5: score += 0.5
    elif obv_s < -5: score -= 0.5

    # Clamp & convert to direction + confidence
    score = max(-9, min(9, score))
    THRESHOLD = 3.0
    if score >= THRESHOLD:
        direction = "BULLISH"
    elif score <= -THRESHOLD:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    # Confidence: scale score magnitude to 50-95%
    conf = min(95, 50 + abs(score) / 9 * 45)

    return {"direction": direction, "score": score, "confidence": round(conf, 1)}


def suggest_options(direction: str, price: float, atr: float, ind: dict) -> dict:
    """Generate strike hint, expiry hint, entry trigger, risk note — from Hop's PredictionResult."""
    if direction == "BULLISH":
        action = "BUY_CALL"
        # ATM or slightly OTM call
        strike = round(price * 1.005 / 5) * 5  # nearest $5 above
        if strike <= price:
            strike += 5
        target = round(price + 3 * atr, 2)
        stop = round(price - 2 * atr, 2)
        expiry = "7-14 DTE (weeklies or next monthly)"
        entry_trigger = f"Price clears ${round(price * 1.005, 2):.2f} on volume ≥ 1.3× avg"
        risk_note = f"Close call if price breaks ${stop:.2f}. Max loss = premium paid."
    elif direction == "BEARISH":
        action = "BUY_PUT"
        # ATM or slightly OTM put
        strike = round(price * 0.995 / 5) * 5
        if strike >= price:
            strike -= 5
        target = round(price - 3 * atr, 2)
        stop = round(price + 2 * atr, 2)
        expiry = "7-14 DTE (weeklies or next monthly)"
        entry_trigger = f"Price breaks ${round(price * 0.995, 2):.2f} on volume ≥ 1.3× avg"
        risk_note = f"Close put if price recovers above ${stop:.2f}. Max loss = premium paid."
    else:
        action = "HOLD"
        strike = round(price / 5) * 5
        target = price
        stop = round(price - 2 * atr, 2)
        expiry = "Wait for clearer setup"
        entry_trigger = "No clear entry — wait for 6/8 agent consensus"
        risk_note = "No position recommended. Market in consolidation."

    vola = ind.get("volatility_20d", 20)
    if vola > 40:
        risk_note += f" High volatility ({vola:.0f}%): size down to 25-50% normal."
    elif vola > 60:
        risk_note += " EXTREME volatility — avoid or use spreads instead."

    return {
        "action": action,
        "strike_hint": f"${strike:.0f} strike",
        "expiry_hint": expiry,
        "entry_trigger": entry_trigger,
        "risk_note": risk_note,
        "target_price": target,
        "stop_price": stop,
    }
