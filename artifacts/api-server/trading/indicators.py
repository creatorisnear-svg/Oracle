"""
Technical Indicators Library — Full Suite
Hop original + ADX, Williams %R, Ichimoku, Fibonacci, Pivot Points, RSI Divergence
"""
import numpy as np
import pandas as pd


def safe_float(val, default=0.0):
    try:
        f = float(val)
        return f if not (np.isnan(f) or np.isinf(f)) else default
    except Exception:
        return default


# ─── Core Indicators ────────────────────────────────────────────────────────

def compute_atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = np.zeros(n)
    if n >= period:
        atr[period-1] = np.mean(tr[:period])
        for i in range(period, n):
            atr[i] = (atr[i-1] * (period-1) + tr[i]) / period
    return atr


def compute_supertrend(highs, lows, closes, period=10, multiplier=3.0):
    atr = compute_atr(highs, lows, closes, period)
    n = len(closes)
    hl2 = (highs + lows) / 2
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr
    final_upper = np.copy(basic_upper)
    final_lower = np.copy(basic_lower)
    direction = np.ones(n)
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


def compute_vwap(highs, lows, closes, volumes):
    typical = (highs + lows + closes) / 3
    cum_tp_vol = np.cumsum(typical * volumes)
    cum_vol = np.cumsum(volumes)
    with np.errstate(divide='ignore', invalid='ignore'):
        vwap = np.where(cum_vol > 0, cum_tp_vol / cum_vol, closes)
    return vwap


def compute_stochastic(highs, lows, closes, period=14):
    stoch = np.full(len(closes), 50.0)
    for i in range(period-1, len(closes)):
        low_min = np.min(lows[i-period+1:i+1])
        high_max = np.max(highs[i-period+1:i+1])
        rng = high_max - low_min
        stoch[i] = ((closes[i] - low_min) / rng * 100) if rng > 0 else 50.0
    return stoch


def compute_rsi(closes, period=14):
    delta = np.diff(closes, prepend=closes[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.zeros(len(closes))
    avg_loss = np.zeros(len(closes))
    if len(closes) > period:
        avg_gain[period] = np.mean(gain[1:period+1])
        avg_loss[period] = np.mean(loss[1:period+1])
        for i in range(period+1, len(closes)):
            avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i-1] * (period-1) + loss[i]) / period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    return 100 - (100 / (1 + rs))


def compute_macd(closes, fast=12, slow=26, signal=9):
    s = pd.Series(closes)
    ema_fast = s.ewm(span=fast, adjust=False).mean().values
    ema_slow = s.ewm(span=slow, adjust=False).mean().values
    macd_line = ema_fast - ema_slow
    sig_line = pd.Series(macd_line).ewm(span=signal, adjust=False).mean().values
    return macd_line, sig_line, macd_line - sig_line


def compute_bollinger(closes, period=20, std_mult=2.0):
    s = pd.Series(closes)
    mid = s.rolling(period).mean().values
    std = s.rolling(period).std().values
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width_pct = np.where(mid > 0, (upper - lower) / mid * 100, 0.0)
    return upper, mid, lower, width_pct


def compute_obv(closes, volumes):
    obv = np.zeros(len(closes))
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv[i] = obv[i-1] + volumes[i]
        elif closes[i] < closes[i-1]:
            obv[i] = obv[i-1] - volumes[i]
        else:
            obv[i] = obv[i-1]
    return obv


def multi_tf_trend_score(closes):
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


# ─── NEW: ADX (Average Directional Index) ─────────────────────────────────────
def compute_adx(highs, lows, closes, period=14):
    """
    ADX measures trend STRENGTH (not direction). ADX > 25 = trending, < 20 = choppy.
    Also returns +DI and -DI for direction.
    """
    n = len(closes)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = highs[i] - highs[i-1]
        dn = lows[i-1] - lows[i]
        plus_dm[i] = up if up > dn and up > 0 else 0
        minus_dm[i] = dn if dn > up and dn > 0 else 0

    atr = compute_atr(highs, lows, closes, period)
    s_plus = pd.Series(plus_dm).ewm(span=period, adjust=False).mean().values
    s_minus = pd.Series(minus_dm).ewm(span=period, adjust=False).mean().values
    s_atr = pd.Series(atr).ewm(span=period, adjust=False).mean().values

    plus_di = np.where(s_atr > 0, 100 * s_plus / s_atr, 0)
    minus_di = np.where(s_atr > 0, 100 * s_minus / s_atr, 0)
    dx = np.where(plus_di + minus_di > 0,
                  100 * np.abs(plus_di - minus_di) / (plus_di + minus_di), 0)
    adx = pd.Series(dx).ewm(span=period, adjust=False).mean().values
    return adx, plus_di, minus_di


# ─── NEW: Williams %R ──────────────────────────────────────────────────────────
def compute_williams_r(highs, lows, closes, period=14):
    """Williams %R: -80 to -100 = oversold (CALL), 0 to -20 = overbought (PUT)."""
    wr = np.full(len(closes), -50.0)
    for i in range(period-1, len(closes)):
        hh = np.max(highs[i-period+1:i+1])
        ll = np.min(lows[i-period+1:i+1])
        rng = hh - ll
        wr[i] = ((hh - closes[i]) / rng * -100) if rng > 0 else -50.0
    return wr


# ─── NEW: Ichimoku Cloud ──────────────────────────────────────────────────────
def compute_ichimoku(highs, lows, closes):
    """
    Tenkan 9, Kijun 26, Senkou B 52.
    Price above cloud = bullish, below = bearish, inside = neutral.
    """
    def midpoint(h, l, period):
        s = pd.Series(h)
        ls = pd.Series(l)
        return ((s.rolling(period).max() + ls.rolling(period).min()) / 2).values

    tenkan = midpoint(highs, lows, 9)
    kijun = midpoint(highs, lows, 26)
    senkou_a = (tenkan + kijun) / 2
    senkou_b = midpoint(highs, lows, 52)

    close = closes[-1]
    sa = safe_float(senkou_a[-1], close)
    sb = safe_float(senkou_b[-1], close)
    cloud_top = max(sa, sb)
    cloud_bot = min(sa, sb)
    t = safe_float(tenkan[-1], close)
    k = safe_float(kijun[-1], close)

    if close > cloud_top:
        signal = "bullish"
    elif close < cloud_bot:
        signal = "bearish"
    else:
        signal = "neutral"

    # TK cross
    tk_cross = None
    if len(tenkan) > 2 and len(kijun) > 2:
        if tenkan[-1] > kijun[-1] and tenkan[-2] <= kijun[-2]:
            tk_cross = "bullish"
        elif tenkan[-1] < kijun[-1] and tenkan[-2] >= kijun[-2]:
            tk_cross = "bearish"

    return {
        "signal": signal,
        "cloud_top": round(cloud_top, 2),
        "cloud_bottom": round(cloud_bot, 2),
        "tenkan": round(t, 2),
        "kijun": round(k, 2),
        "tk_cross": tk_cross,
    }


# ─── NEW: Fibonacci Retracement ───────────────────────────────────────────────
def compute_fibonacci(highs, lows, period=50):
    """Key Fibonacci levels from recent swing high/low."""
    n = min(period, len(highs))
    h = np.max(highs[-n:])
    l = np.min(lows[-n:])
    diff = h - l
    if diff == 0:
        return {}
    return {
        "high": round(h, 2),
        "low": round(l, 2),
        "fib_0": round(l, 2),
        "fib_236": round(l + 0.236 * diff, 2),
        "fib_382": round(l + 0.382 * diff, 2),
        "fib_500": round(l + 0.500 * diff, 2),
        "fib_618": round(l + 0.618 * diff, 2),  # Golden ratio — highest probability
        "fib_786": round(l + 0.786 * diff, 2),
        "fib_100": round(h, 2),
    }


# ─── NEW: Pivot Points & Support/Resistance ───────────────────────────────────
def compute_pivot_levels(highs, lows, closes, period=5):
    """Classic pivot points from recent data."""
    n = min(period, len(highs))
    h = np.max(highs[-n:])
    l = np.min(lows[-n:])
    c = closes[-1]
    pivot = (h + l + c) / 3
    r1 = 2 * pivot - l
    r2 = pivot + (h - l)
    r3 = h + 2 * (pivot - l)
    s1 = 2 * pivot - h
    s2 = pivot - (h - l)
    s3 = l - 2 * (h - pivot)
    return {
        "pivot": round(pivot, 2),
        "r1": round(r1, 2), "r2": round(r2, 2), "r3": round(r3, 2),
        "s1": round(s1, 2), "s2": round(s2, 2), "s3": round(s3, 2),
    }


# ─── NEW: RSI Divergence Detection ────────────────────────────────────────────
def detect_rsi_divergence(closes, rsi_vals, lookback=20):
    """
    Bullish divergence: price lower low + RSI higher low → likely bounce up (CALL).
    Bearish divergence: price higher high + RSI lower high → likely reversal down (PUT).
    Returns: "bullish", "bearish", or "none"
    """
    n = len(closes)
    if n < lookback + 5:
        return "none"

    half = lookback // 2
    p_early = closes[n-lookback:n-half]
    p_late = closes[n-half:n]
    r_early = rsi_vals[n-lookback:n-half]
    r_late = rsi_vals[n-half:n]

    if len(p_early) == 0 or len(p_late) == 0:
        return "none"

    # Bullish: price lower low, RSI higher low
    p_early_min = np.min(p_early)
    p_late_min = np.min(p_late)
    r_early_at_low = r_early[np.argmin(p_early)]
    r_late_at_low = r_late[np.argmin(p_late)]

    if p_late_min < p_early_min and r_late_at_low > r_early_at_low + 4:
        return "bullish"

    # Bearish: price higher high, RSI lower high
    p_early_max = np.max(p_early)
    p_late_max = np.max(p_late)
    r_early_at_high = r_early[np.argmax(p_early)]
    r_late_at_high = r_late[np.argmax(p_late)]

    if p_late_max > p_early_max and r_late_at_high < r_early_at_high - 4:
        return "bearish"

    return "none"


# ─── NEW: Donchian Channels ───────────────────────────────────────────────────
def compute_donchian(highs, lows, closes, period=20):
    """20-day high/low channel. Breakouts = trend-follow signals (Turtle rules)."""
    n = len(closes)
    if n < period + 1:
        return {"upper": float(closes[-1]), "lower": float(closes[-1]),
                "mid": float(closes[-1]), "break": "none"}
    upper = float(np.max(highs[-period-1:-1]))   # rolling max EXCLUDING today
    lower = float(np.min(lows[-period-1:-1]))    # rolling min EXCLUDING today
    mid = (upper + lower) / 2
    c = float(closes[-1])
    brk = "none"
    if c > upper:
        brk = "bullish"
    elif c < lower:
        brk = "bearish"
    return {"upper": round(upper, 2), "lower": round(lower, 2),
            "mid": round(mid, 2), "break": brk}


# ─── NEW: Keltner Channels ────────────────────────────────────────────────────
def compute_keltner(closes, atr_vals, period=20, mult=2.0):
    """EMA20 ± 2×ATR. Trend channel — closes outside = strong directional move."""
    if len(closes) < period:
        return {"upper": float(closes[-1]), "lower": float(closes[-1]),
                "mid": float(closes[-1]), "break": "none"}
    ema = pd.Series(closes).ewm(span=period, adjust=False).mean().values
    a = float(atr_vals[-1])
    m = float(ema[-1])
    upper = m + mult * a
    lower = m - mult * a
    c = float(closes[-1])
    brk = "none"
    if c > upper:
        brk = "bullish"
    elif c < lower:
        brk = "bearish"
    return {"upper": round(upper, 2), "lower": round(lower, 2),
            "mid": round(m, 2), "break": brk}


# ─── NEW: Head & Shoulders (and inverse) detection ────────────────────────────
def detect_head_shoulders(highs, lows, closes, lookback=40):
    """
    Classic 5-pivot pattern.
    H&S top:    LS_high < Head_high > RS_high, shoulders within ~3% of each other,
                neckline = avg of the two troughs, breakdown when close < neckline.
    Inverse:    mirror of above.
    Returns "bearish_top", "bullish_inverse", or "none".
    """
    n = len(closes)
    if n < lookback:
        return "none"
    h = highs[-lookback:]
    l = lows[-lookback:]
    c = closes[-1]

    # find local peaks/troughs (window = 3)
    peaks = [i for i in range(2, len(h)-2) if h[i] > h[i-1] and h[i] > h[i-2]
             and h[i] > h[i+1] and h[i] > h[i+2]]
    troughs = [i for i in range(2, len(l)-2) if l[i] < l[i-1] and l[i] < l[i-2]
               and l[i] < l[i+1] and l[i] < l[i+2]]

    # H&S top: 3 peaks with middle highest, 2 troughs between them
    if len(peaks) >= 3 and len(troughs) >= 2:
        p3 = peaks[-3:]
        if h[p3[1]] > h[p3[0]] and h[p3[1]] > h[p3[2]] \
           and abs(h[p3[0]] - h[p3[2]]) / h[p3[1]] < 0.04:
            inner_troughs = [t for t in troughs if p3[0] < t < p3[2]]
            if len(inner_troughs) >= 2:
                neckline = (l[inner_troughs[0]] + l[inner_troughs[-1]]) / 2
                if c < neckline:
                    return "bearish_top"

    # Inverse H&S: 3 troughs with middle lowest, 2 peaks between
    if len(troughs) >= 3 and len(peaks) >= 2:
        t3 = troughs[-3:]
        if l[t3[1]] < l[t3[0]] and l[t3[1]] < l[t3[2]] \
           and abs(l[t3[0]] - l[t3[2]]) / l[t3[1]] < 0.04:
            inner_peaks = [p for p in peaks if t3[0] < p < t3[2]]
            if len(inner_peaks) >= 2:
                neckline = (h[inner_peaks[0]] + h[inner_peaks[-1]]) / 2
                if c > neckline:
                    return "bullish_inverse"

    return "none"


# ─── Composite Indicator Bundle ────────────────────────────────────────────────
def compute_all_indicators(df: pd.DataFrame) -> dict:
    closes = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    volumes = df["Volume"].fillna(0).values.astype(float)
    n = len(closes)

    # Core indicators
    atr = compute_atr(highs, lows, closes)
    supertrend_vals, st_dir = compute_supertrend(highs, lows, closes)
    vwap = compute_vwap(highs, lows, closes, volumes)
    stoch = compute_stochastic(highs, lows, closes)
    rsi = compute_rsi(closes)
    macd_line, macd_sig, macd_hist = compute_macd(closes)
    bb_upper, bb_mid, bb_lower, bb_width = compute_bollinger(closes)
    obv = compute_obv(closes, volumes)
    trend_score = multi_tf_trend_score(closes)

    # New indicators
    adx_vals, plus_di, minus_di = compute_adx(highs, lows, closes)
    wr = compute_williams_r(highs, lows, closes)
    ichimoku = compute_ichimoku(highs, lows, closes)
    fibonacci = compute_fibonacci(highs, lows)
    pivots = compute_pivot_levels(highs, lows, closes)
    rsi_div = detect_rsi_divergence(closes, rsi)
    donchian = compute_donchian(highs, lows, closes)
    keltner = compute_keltner(closes, atr)
    head_shoulders = detect_head_shoulders(highs, lows, closes)

    # Volume stats
    avg_vol_20 = np.mean(volumes[-20:]) if n >= 20 else np.mean(volumes) if n > 0 else 1
    rel_vol = volumes[-1] / avg_vol_20 if avg_vol_20 > 0 else 1.0
    vol_trend_5v20 = (np.mean(volumes[-5:]) / avg_vol_20) if n >= 20 else 1.0
    up_vol = sum(volumes[i] for i in range(max(n-20, 1), n) if closes[i] > closes[i-1])
    dn_vol = sum(volumes[i] for i in range(max(n-20, 1), n) if closes[i] < closes[i-1])
    up_dn_ratio = up_vol / dn_vol if dn_vol > 0 else 1.0
    obv_slope = (obv[-1] - obv[-10]) / (abs(obv[-10]) + 1e-9) * 100 if n >= 10 else 0.0

    price = closes[-1]
    prev_close = closes[-2] if n >= 2 else closes[-1]
    change_1d = (price - prev_close) / prev_close * 100 if prev_close else 0
    change_5d = (price - closes[-5]) / closes[-5] * 100 if n >= 5 else 0

    st_flips = 0
    for i in range(n-2, max(n-20, 0), -1):
        if st_dir[i] != st_dir[i+1]:
            st_flips = n - 1 - i
            break

    price_vs_vwap = (price - vwap[-1]) / vwap[-1] * 100 if vwap[-1] > 0 else 0

    vol_confirm = "neutral"
    if volumes[-1] > avg_vol_20 * 1.2:
        vol_confirm = "confirms"
    elif volumes[-1] < avg_vol_20 * 0.8:
        vol_confirm = "diverges"

    # Nearest Fibonacci level to current price
    near_fib = None
    if fibonacci:
        fib_levels = [v for k, v in fibonacci.items() if k.startswith("fib_") and isinstance(v, (int, float))]
        if fib_levels:
            dists = [(abs(price - lvl), lvl) for lvl in fib_levels]
            dists.sort()
            near_fib = dists[0][1]

    # Price vs pivot levels
    pivot_bias = "neutral"
    if pivots:
        if price > pivots.get("r1", price):
            pivot_bias = "bullish_breakout"
        elif price > pivots.get("pivot", price):
            pivot_bias = "bullish"
        elif price < pivots.get("s1", price):
            pivot_bias = "bearish_breakdown"
        elif price < pivots.get("pivot", price):
            pivot_bias = "bearish"

    return {
        "price": price,
        "prev_close": prev_close,
        "change_1d": change_1d,
        "change_5d": change_5d,
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
        "macd_cross_up": bool(macd_line[-1] > macd_sig[-1] and macd_line[-2] <= macd_sig[-2]) if n >= 2 else False,
        "macd_cross_dn": bool(macd_line[-1] < macd_sig[-1] and macd_line[-2] >= macd_sig[-2]) if n >= 2 else False,
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
        "supertrend_dir": "up" if st_dir[-1] > 0 else "down",
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
        # ── NEW ──────────────────────────────────────────────────────
        "adx": safe_float(adx_vals[-1]),
        "adx_trending": bool(adx_vals[-1] > 25),
        "plus_di": safe_float(plus_di[-1]),
        "minus_di": safe_float(minus_di[-1]),
        "williams_r": safe_float(wr[-1]),
        "ichimoku": ichimoku,
        "ichimoku_signal": ichimoku["signal"],
        "ichimoku_tk_cross": ichimoku.get("tk_cross"),
        "fibonacci": fibonacci,
        "near_fib_level": safe_float(near_fib) if near_fib else 0,
        "pivot_levels": pivots,
        "pivot_bias": pivot_bias,
        "rsi_divergence": rsi_div,
        "stoch_k": safe_float(stoch[-1]),
        "donchian": donchian,
        "keltner": keltner,
        "head_shoulders": head_shoulders,
    }


def score_indicators_to_direction(ind: dict) -> dict:
    """
    Full composite score → directional call.
    Adapted from Hop marketBacktest.ts scoreIndicators().
    """
    score = 0.0

    # 1. Multi-TF trend
    score += ind.get("trend_score", 0)

    # 2. RSI (tiered)
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

    # 6. VWAP
    pvwap = ind.get("price_vs_vwap_pct", 0)
    if pvwap > 0.5: score += 0.5
    elif pvwap < -0.5: score -= 0.5

    # 7. Stochastic
    stoch = ind.get("stoch_k", 50)
    if stoch >= 80: score += 1.0
    elif stoch >= 70: score += 0.5
    elif stoch <= 20: score -= 1.0
    elif stoch <= 30: score -= 0.5

    # 8. Volume
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

    # ── NEW INDICATORS ──────────────────────────────────────────────
    # 10. ADX — only boost score when market is clearly trending
    adx = ind.get("adx", 20)
    if adx > 35: score *= 1.1  # Strong trend, amplify
    elif adx < 15: score *= 0.7  # Choppy, dampen

    # 11. Williams %R
    wr = ind.get("williams_r", -50)
    if wr >= -20: score += 1.0    # Overbought → bearish
    elif wr <= -80: score -= 1.0  # Oversold → bullish (mean-reversion)
    if wr <= -80: score += 1.5    # Deep oversold = CALL signal
    if wr >= -20: score -= 1.5    # Deep overbought = PUT signal

    # 12. Ichimoku
    ichi = ind.get("ichimoku_signal", "neutral")
    if ichi == "bullish": score += 1.5
    elif ichi == "bearish": score -= 1.5
    if ind.get("ichimoku_tk_cross") == "bullish": score += 1.0
    elif ind.get("ichimoku_tk_cross") == "bearish": score -= 1.0

    # 13. RSI Divergence (powerful early signal)
    div = ind.get("rsi_divergence", "none")
    if div == "bullish": score += 2.0
    elif div == "bearish": score -= 2.0

    # 14. Pivot bias
    pb = ind.get("pivot_bias", "neutral")
    if pb == "bullish_breakout": score += 1.0
    elif pb == "bullish": score += 0.4
    elif pb == "bearish_breakdown": score -= 1.0
    elif pb == "bearish": score -= 0.4

    # 15. +DI vs -DI
    plus_di = ind.get("plus_di", 25)
    minus_di = ind.get("minus_di", 25)
    if plus_di > minus_di + 5: score += 0.5
    elif minus_di > plus_di + 5: score -= 0.5

    score = max(-12, min(12, score))
    THRESHOLD = 3.5
    if score >= THRESHOLD:
        direction = "BULLISH"
    elif score <= -THRESHOLD:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    conf = min(95, 45 + abs(score) / 12 * 50)
    return {"direction": direction, "score": score, "confidence": round(conf, 1)}


def _get_option_expiries():
    """Compute real upcoming options expiry Fridays."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # Next Friday (at least 1 day away)
    d = now + timedelta(days=1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    weekly = d.strftime("%b %d")
    biweekly = (d + timedelta(days=7)).strftime("%b %d")
    # 3rd Friday of next month
    nm = now.month % 12 + 1
    ny = now.year if now.month < 12 else now.year + 1
    m = datetime(ny, nm, 1, tzinfo=timezone.utc)
    fc = 0
    while True:
        if m.weekday() == 4:
            fc += 1
            if fc == 3:
                break
        m += timedelta(days=1)
    monthly = m.strftime("%b %d")
    return weekly, biweekly, monthly


def suggest_options(direction: str, price: float, atr: float, ind: dict) -> dict:
    fib = ind.get("fibonacci", {})
    pivots = ind.get("pivot_levels", {})
    weekly, biweekly, monthly = _get_option_expiries()

    if direction == "BULLISH":
        action = "BUY_CALL"
        strike_raw = pivots.get("r1", price * 1.005)
        strike = round(strike_raw / 5) * 5
        if strike <= price: strike += 5
        target = round(price + 3 * atr, 2)
        stop = round(price - 2 * atr, 2)
        fib618 = fib.get("fib_618", 0)
        conf_boost = "61.8% Fib support near entry. " if fib618 and abs(price - fib618) / price < 0.02 else ""
        expiry = f"Weekly: {weekly}  |  2-Week: {biweekly}  |  Monthly: {monthly}"
        entry_trigger = f"{conf_boost}Price clears ${round(price * 1.005, 2):.2f} on volume ≥ 1.3× avg"
        risk_note = f"Close call if price breaks ${stop:.2f} (2×ATR). Max loss = premium paid."
    elif direction == "BEARISH":
        action = "BUY_PUT"
        strike_raw = pivots.get("s1", price * 0.995)
        strike = round(strike_raw / 5) * 5
        if strike >= price: strike -= 5
        target = round(price - 3 * atr, 2)
        stop = round(price + 2 * atr, 2)
        fib382 = fib.get("fib_382", 0)
        conf_boost = "38.2% Fib resistance near entry. " if fib382 and abs(price - fib382) / price < 0.02 else ""
        expiry = f"Weekly: {weekly}  |  2-Week: {biweekly}  |  Monthly: {monthly}"
        entry_trigger = f"{conf_boost}Price breaks ${round(price * 0.995, 2):.2f} on volume ≥ 1.3× avg"
        risk_note = f"Close put if price recovers above ${stop:.2f} (2×ATR). Max loss = premium paid."
    else:
        action = "HOLD"
        strike = round(price / 5) * 5
        target = price
        stop = round(price - 2 * atr, 2)
        expiry = f"Next setups: {weekly} / {biweekly} / {monthly}"
        entry_trigger = "No clear entry — wait for 5/9 agent consensus"
        risk_note = "No position recommended. Mixed signals or choppy market."

    vola = ind.get("volatility_20d", 20)
    if vola > 50:
        risk_note += f" EXTREME vol ({vola:.0f}%): use spreads."
    elif vola > 35:
        risk_note += f" High vol ({vola:.0f}%): size down 50%."

    return {
        "action": action,
        "strike_hint": f"${strike:.0f} strike",
        "expiry_hint": expiry,
        "expiry_weekly": weekly,
        "expiry_biweekly": biweekly,
        "expiry_monthly": monthly,
        "entry_trigger": entry_trigger,
        "risk_note": risk_note,
        "target_price": target,
        "stop_price": stop,
    }
