"""
Technical Indicators Library — Full Suite
Hop original + ADX, Williams %R, Ichimoku, Fibonacci, Pivot Points, RSI Divergence
"""
import math
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
    # Suppress divide-by-zero warning — np.where masks the result already,
    # but the division is computed before the mask. Silencing keeps the
    # diagnostics log readable.
    with np.errstate(divide="ignore", invalid="ignore"):
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

    # Same divide-by-zero suppression pattern — masked with np.where but the
    # division still runs first, generating warnings on early bars where
    # ATR is still zero / NaN.
    with np.errstate(divide="ignore", invalid="ignore"):
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


# ─── NEW: Parabolic SAR ───────────────────────────────────────────────────────
def compute_parabolic_sar(highs, lows, af_step=0.02, af_max=0.2):
    """Parabolic SAR — trend-following stop-and-reverse system."""
    n = len(highs)
    if n < 3:
        return {"sar": float(highs[-1] if n else 0), "trend": "neutral"}
    sar = np.zeros(n)
    trend = 1  # 1 = up, -1 = down
    af = af_step
    ep = highs[0]
    sar[0] = lows[0]
    for i in range(1, n):
        prev = sar[i-1]
        sar[i] = prev + af * (ep - prev)
        if trend == 1:
            sar[i] = min(sar[i], lows[i-1], lows[i-2] if i >= 2 else lows[i-1])
            if lows[i] < sar[i]:
                trend = -1
                sar[i] = ep
                ep = lows[i]
                af = af_step
            elif highs[i] > ep:
                ep = highs[i]
                af = min(af + af_step, af_max)
        else:
            sar[i] = max(sar[i], highs[i-1], highs[i-2] if i >= 2 else highs[i-1])
            if highs[i] > sar[i]:
                trend = 1
                sar[i] = ep
                ep = highs[i]
                af = af_step
            elif lows[i] < ep:
                ep = lows[i]
                af = min(af + af_step, af_max)
    return {"sar": float(sar[-1]), "trend": "up" if trend == 1 else "down"}


# ─── NEW: Aroon Oscillator ────────────────────────────────────────────────────
def compute_aroon(highs, lows, period=14):
    """Aroon Up/Down — measures time since highest high / lowest low."""
    n = len(highs)
    if n < period:
        return {"up": 50.0, "down": 50.0, "osc": 0.0}
    h_recent = highs[-period:]
    l_recent = lows[-period:]
    days_since_high = period - 1 - int(np.argmax(h_recent))
    days_since_low = period - 1 - int(np.argmin(l_recent))
    aroon_up = 100 * (period - days_since_high) / period
    aroon_dn = 100 * (period - days_since_low) / period
    return {"up": round(aroon_up, 1), "down": round(aroon_dn, 1),
            "osc": round(aroon_up - aroon_dn, 1)}


# ─── NEW: CCI (Commodity Channel Index) ───────────────────────────────────────
def compute_cci(highs, lows, closes, period=20):
    """CCI — overbought >100, oversold <-100."""
    n = len(closes)
    if n < period:
        return 0.0
    tp = (highs + lows + closes) / 3
    sma = pd.Series(tp).rolling(period).mean().values
    md = pd.Series(tp).rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True).values
    cci = (tp - sma) / (0.015 * md + 1e-9)
    return float(cci[-1])


# ─── NEW: MFI (Money Flow Index) — volume-weighted RSI ────────────────────────
def compute_mfi(highs, lows, closes, volumes, period=14):
    """MFI 0-100. >80 overbought, <20 oversold. Confirms RSI with volume."""
    n = len(closes)
    if n < period + 1:
        return 50.0
    tp = (highs + lows + closes) / 3
    mf = tp * volumes
    pos_mf = np.zeros(n)
    neg_mf = np.zeros(n)
    for i in range(1, n):
        if tp[i] > tp[i-1]:
            pos_mf[i] = mf[i]
        elif tp[i] < tp[i-1]:
            neg_mf[i] = mf[i]
    pos_sum = np.sum(pos_mf[-period:])
    neg_sum = np.sum(neg_mf[-period:])
    if neg_sum == 0:
        return 100.0
    mr = pos_sum / neg_sum
    return float(100 - (100 / (1 + mr)))


# ─── NEW: CMF (Chaikin Money Flow) ────────────────────────────────────────────
def compute_cmf(highs, lows, closes, volumes, period=20):
    """CMF — money flow over period. Positive = accumulation, negative = distribution."""
    n = len(closes)
    if n < period:
        return 0.0
    rng = highs - lows
    mf_mult = np.where(rng > 0, ((closes - lows) - (highs - closes)) / rng, 0)
    mf_vol = mf_mult * volumes
    cmf = np.sum(mf_vol[-period:]) / (np.sum(volumes[-period:]) + 1e-9)
    return float(cmf)


# ─── NEW: TSI (True Strength Index) ───────────────────────────────────────────
def compute_tsi(closes, long_p=25, short_p=13):
    """TSI — double-smoothed momentum oscillator."""
    n = len(closes)
    if n < long_p + short_p + 5:
        return 0.0
    delta = np.diff(closes, prepend=closes[0])
    s = pd.Series(delta).ewm(span=long_p, adjust=False).mean()
    s = s.ewm(span=short_p, adjust=False).mean()
    a = pd.Series(np.abs(delta)).ewm(span=long_p, adjust=False).mean()
    a = a.ewm(span=short_p, adjust=False).mean()
    tsi = 100 * s / (a + 1e-9)
    return float(tsi.values[-1])


# ─── NEW: TRIX — triple-smoothed EMA momentum ─────────────────────────────────
def compute_trix(closes, period=15):
    n = len(closes)
    if n < period * 3:
        return 0.0
    s = pd.Series(closes).ewm(span=period, adjust=False).mean()
    s = s.ewm(span=period, adjust=False).mean()
    s = s.ewm(span=period, adjust=False).mean()
    trix = s.pct_change() * 100
    return float(trix.values[-1])


# ─── NEW: HMA (Hull Moving Average) — fast, low-lag MA ────────────────────────
def compute_hma(closes, period=20):
    n = len(closes)
    if n < period * 2:
        return float(closes[-1])
    half = period // 2
    sqrtp = int(np.sqrt(period))
    wma1 = pd.Series(closes).rolling(half).apply(
        lambda x: np.dot(x, np.arange(1, half+1)) / (half*(half+1)/2), raw=True)
    wma2 = pd.Series(closes).rolling(period).apply(
        lambda x: np.dot(x, np.arange(1, period+1)) / (period*(period+1)/2), raw=True)
    diff = (2 * wma1 - wma2)
    hma = diff.rolling(sqrtp).apply(
        lambda x: np.dot(x, np.arange(1, sqrtp+1)) / (sqrtp*(sqrtp+1)/2), raw=True)
    return float(hma.values[-1]) if not np.isnan(hma.values[-1]) else float(closes[-1])


# ─── NEW: Pure ROC (Rate of Change) ───────────────────────────────────────────
def compute_roc(closes, period=10):
    if len(closes) <= period:
        return 0.0
    return float((closes[-1] - closes[-period-1]) / closes[-period-1] * 100)


# ─── NEW: Double Top / Double Bottom detection ────────────────────────────────
def detect_double_pattern(highs, lows, closes, lookback=30, tol=0.025):
    """Two peaks/troughs at similar level + break of valley/peak between them."""
    n = len(closes)
    if n < lookback:
        return "none"
    h, l, c = highs[-lookback:], lows[-lookback:], closes[-1]
    peaks = [i for i in range(2, len(h)-2) if h[i] > h[i-1] and h[i] > h[i-2]
             and h[i] > h[i+1] and h[i] > h[i+2]]
    troughs = [i for i in range(2, len(l)-2) if l[i] < l[i-1] and l[i] < l[i-2]
               and l[i] < l[i+1] and l[i] < l[i+2]]
    # Double top
    if len(peaks) >= 2:
        p1, p2 = peaks[-2], peaks[-1]
        if abs(h[p1] - h[p2]) / h[p1] < tol:
            valley_lows = l[p1:p2+1]
            if len(valley_lows) > 0:
                valley = float(np.min(valley_lows))
                if c < valley:
                    return "double_top"
    # Double bottom
    if len(troughs) >= 2:
        t1, t2 = troughs[-2], troughs[-1]
        if abs(l[t1] - l[t2]) / l[t1] < tol:
            peak_highs = h[t1:t2+1]
            if len(peak_highs) > 0:
                peak = float(np.max(peak_highs))
                if c > peak:
                    return "double_bottom"
    return "none"


# ─── NEW: Triangle pattern (ascending / descending) ───────────────────────────
def detect_triangle(highs, lows, lookback=25):
    """Ascending = flat highs + rising lows. Descending = flat lows + falling highs."""
    n = len(highs)
    if n < lookback:
        return "none"
    h = highs[-lookback:]
    l = lows[-lookback:]
    x = np.arange(lookback)
    # linear regression slopes
    slope_h = np.polyfit(x, h, 1)[0] / np.mean(h) * 100  # %/bar
    slope_l = np.polyfit(x, l, 1)[0] / np.mean(l) * 100
    if abs(slope_h) < 0.05 and slope_l > 0.10:
        return "ascending"   # bullish
    if abs(slope_l) < 0.05 and slope_h < -0.10:
        return "descending"  # bearish
    if slope_h < -0.05 and slope_l > 0.05:
        return "symmetrical"  # neutral, breakout coming
    return "none"


# ─── NEW: Cup & Handle (simplified) ───────────────────────────────────────────
def detect_cup_handle(closes, highs, lookback=60):
    """Rounded U-shape (cup) followed by a small consolidation (handle), break on top."""
    n = len(closes)
    if n < lookback:
        return "none"
    seg = closes[-lookback:]
    third = lookback // 3
    left, mid, right = seg[:third], seg[third:2*third], seg[2*third:]
    if len(left) == 0 or len(mid) == 0 or len(right) == 0:
        return "none"
    left_max = np.max(left); right_max = np.max(right)
    mid_min = np.min(mid)
    # Cup: similar highs on left and right, dip in middle (≥ 8% drawdown)
    cup = (abs(left_max - right_max) / left_max < 0.04 and
           (left_max - mid_min) / left_max > 0.08)
    if not cup:
        return "none"
    # Handle: small pullback in last 5-10 bars then break above cup rim
    rim = max(left_max, right_max)
    if closes[-1] > rim and np.min(seg[-10:]) > rim * 0.97:
        return "cup_handle"
    return "none"


# ─── NEW: Kalman 1-D smoother on price ────────────────────────────────────────
def kalman_smoothed_trend(closes, process_var=1e-4, meas_var=1e-2):
    """Returns the latest Kalman-smoothed price + slope (denoised trend)."""
    n = len(closes)
    if n < 5:
        return {"smoothed": float(closes[-1]), "slope_pct": 0.0}
    x = float(closes[0])
    p = 1.0
    history = [x]
    for z in closes[1:]:
        # predict
        p = p + process_var
        # update
        k = p / (p + meas_var)
        x = x + k * (float(z) - x)
        p = (1 - k) * p
        history.append(x)
    last = history[-1]
    prev = history[-min(10, len(history))]
    slope_pct = (last - prev) / prev * 100 if prev else 0.0
    return {"smoothed": float(last), "slope_pct": float(slope_pct)}


# ─── NEW: Monte Carlo target-hit probability (simple GBM) ─────────────────────
def monte_carlo_target_prob(price, target, vol_pct, days=7, n_sims=2000):
    """Probability of price touching `target` within `days` trading days using GBM."""
    if price <= 0 or vol_pct <= 0 or days <= 0:
        return 0.5
    sigma_d = (vol_pct / 100) / np.sqrt(252)  # daily vol
    direction_up = target > price
    hits = 0
    rng = np.random.default_rng(42)
    for _ in range(n_sims):
        p = price
        for _ in range(days):
            p *= np.exp(rng.normal(0, sigma_d))
            if (direction_up and p >= target) or (not direction_up and p <= target):
                hits += 1
                break
    return float(hits / n_sims)


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
    psar = compute_parabolic_sar(highs, lows)
    aroon = compute_aroon(highs, lows)
    cci_val = compute_cci(highs, lows, closes)
    mfi_val = compute_mfi(highs, lows, closes, volumes)
    cmf_val = compute_cmf(highs, lows, closes, volumes)
    tsi_val = compute_tsi(closes)
    trix_val = compute_trix(closes)
    hma_val = compute_hma(closes)
    roc10 = compute_roc(closes, 10)
    roc20 = compute_roc(closes, 20)
    double_pat = detect_double_pattern(highs, lows, closes)
    triangle_pat = detect_triangle(highs, lows)
    cup_handle_pat = detect_cup_handle(closes, highs)
    kalman = kalman_smoothed_trend(closes)

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
        "psar": psar,
        "aroon": aroon,
        "cci": safe_float(cci_val),
        "mfi": safe_float(mfi_val),
        "cmf": safe_float(cmf_val),
        "tsi": safe_float(tsi_val),
        "trix": safe_float(trix_val),
        "hma20": safe_float(hma_val),
        "roc10": safe_float(roc10),
        "roc20": safe_float(roc20),
        "double_pattern": double_pat,
        "triangle_pattern": triangle_pat,
        "cup_handle": cup_handle_pat,
        "kalman_trend": kalman,
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


# ── Real-world option strike increments (CBOE/Nasdaq listed strikes) ─────
# These are the actual tick sizes used by the exchanges. Picking a strike
# at a tick that doesn't trade leaves the user staring at a "no quote"
# screen on their broker. Bands chosen from the most common listed
# patterns across optionable US equities + ETFs.
def _strike_tick(price: float) -> float:
    if price < 25:    return 0.5
    if price < 200:   return 1.0
    if price < 500:   return 2.5
    if price < 1000:  return 5.0
    return 10.0


def _round_to_tick(value: float, tick: float) -> float:
    """Round to the nearest multiple of `tick` (handles fractional ticks)."""
    if tick <= 0:
        return round(value, 2)
    return round(round(value / tick) * tick, 2)


def _round_to_tick_directional(value: float, tick: float, mode: str) -> float:
    """Round to a tick-aligned strike while preserving moneyness intent.

    `mode` controls the rounding direction:
      • "down"    – floor to tick (ITM call, OTM put — strike ≤ value)
      • "up"      – ceil  to tick (ITM put,  OTM call — strike ≥ value)
      • "nearest" – nearest tick (ATM)

    Why this matters: nearest-tick rounding can flip moneyness. e.g. SPY
    at $713.98 with a "slight ITM call" target of $712 rounds *up* to
    the $715 tick — which is OTM, contradicting the label and giving the
    user a delta they didn't expect. Floor for ITM calls keeps strike
    ≤ spot so the contract is genuinely in the money.
    """
    if tick <= 0:
        return round(value, 2)
    if mode == "down":
        return round(math.floor(value / tick) * tick, 2)
    if mode == "up":
        return round(math.ceil(value / tick) * tick, 2)
    return round(round(value / tick) * tick, 2)


def _horizon_moneyness(horizon_key: str, confidence: float) -> tuple[str, float]:
    """Pick how far ITM/OTM the strike should sit, in ATR units.

    Returns (label, atr_offset).  Positive offset = ITM (strike under spot
    for calls / above spot for puts).  Negative = OTM.

    Rule of thumb the desk uses:
      • intraday / 0DTE → ATM. Pure gamma play, low absolute premium,
        most leverage from a small move. Going ITM eats too much capital
        for a 2-hour scalp.
      • day              → ATM (slight ITM if very high conviction).
      • swing            → slight ITM (≈0.5 ATR). Better delta, less theta
        decay over 1-5 days, still cheap enough to swing-size.
      • position         → deeper ITM (≈1.0 ATR). Acts more like stock
        with leverage; far less affected by IV crush over 1-3 weeks.
    Confidence nudges the offset within ±0.4 ATR — high conviction can
    afford to pay for ITM, low conviction stays cheaper near ATM.
    """
    base = {
        "intraday": (0.0, "ATM"),
        "day":      (0.0, "ATM"),
        "swing":    (0.5, "slight ITM"),
        "position": (1.0, "ITM"),
    }.get((horizon_key or "swing").lower(), (0.5, "slight ITM"))
    offset, label = base
    # Conviction tilt: 80%+ adds 0.3 ATR of ITM, 50% subtracts 0.3 ATR
    tilt = (confidence - 65.0) / 100.0 * 0.6
    offset = max(-0.5, min(1.5, offset + tilt))
    if offset >= 0.75:
        label = "ITM"
    elif offset >= 0.25:
        label = "slight ITM"
    elif offset >= -0.25:
        label = "ATM"
    else:
        label = "slight OTM"
    return label, offset


def _estimate_premium(price: float, strike: float, atr: float, days_to_expiry: int,
                      direction: str) -> float:
    """Cheap closed-form premium estimate, no IV lookup needed.

    Uses ATR as a daily-vol proxy and the standard ATM Black-Scholes
    approximation `C_atm ≈ 0.4 × σ × S × √T`. For ITM/OTM we add/subtract
    the intrinsic value. Good enough for a "what will this contract cost
    me?" sanity check in the UI — within ~25% of the real mid for liquid
    weekly contracts in normal vol regimes.
    """
    if price <= 0 or atr <= 0 or days_to_expiry <= 0:
        return 0.0
    # Annualise: ATR is daily, T is in trading-days fraction of a year (252)
    sigma_daily = atr / price
    t_years = days_to_expiry / 252.0
    atm_value = 0.4 * sigma_daily * price * math.sqrt(max(t_years, 1e-6)) * math.sqrt(252.0)
    if direction == "BULLISH":
        intrinsic = max(0.0, price - strike)
    else:  # BEARISH
        intrinsic = max(0.0, strike - price)
    # Premium = intrinsic + time value. ATM has only time value;
    # deep ITM is mostly intrinsic with a small time premium on top.
    moneyness = abs(strike - price) / max(price, 1e-6)
    time_value = atm_value * math.exp(-1.5 * moneyness)
    return round(max(0.05, intrinsic + time_value), 2)


def _estimate_delta(price: float, strike: float, atr: float, days_to_expiry: int,
                    direction: str) -> float:
    """Approx delta from N(d1) without scipy in the hot path.

    Uses an erf-based normal CDF — accurate to ~3 decimal places. Good
    enough for a UI hint ("about 0.62 delta"); not for hedging.
    """
    if price <= 0 or strike <= 0 or atr <= 0 or days_to_expiry <= 0:
        return 0.5
    sigma_annual = (atr / price) * math.sqrt(252.0)
    t_years = days_to_expiry / 252.0
    denom = sigma_annual * math.sqrt(max(t_years, 1e-6))
    if denom <= 0:
        return 0.5 if direction == "BULLISH" else -0.5
    d1 = (math.log(price / strike) + 0.5 * sigma_annual * sigma_annual * t_years) / denom
    # erf-based normal CDF
    n_d1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    if direction == "BULLISH":
        return round(n_d1, 2)
    else:
        return round(n_d1 - 1.0, 2)


def suggest_options(direction: str, price: float, atr: float, ind: dict,
                    horizon_key: str = "swing", confidence: float = 65.0) -> dict:
    """Build the options trade card.

    Strike, premium, delta, breakeven and moneyness label are now derived
    from real-world tick increments (per-price-band) and a horizon-aware
    moneyness target. Replaces the old `round to nearest $5` heuristic
    which produced strikes like "$185" on a $187 stock that doesn't list
    $185 strikes — broker would show "no quote".
    """
    fib = ind.get("fibonacci", {})
    weekly, biweekly, monthly = _get_option_expiries()
    tick = _strike_tick(price)

    # Pick a primary expiry to attach premium/breakeven estimates to.
    # 0DTE/intraday → weekly, swing → 2-week, position → monthly.
    expiry_dte_map = {
        "intraday": (weekly, 7),
        "day":      (weekly, 7),
        "swing":    (biweekly, 14),
        "position": (monthly, 30),
    }
    primary_exp, primary_dte = expiry_dte_map.get((horizon_key or "swing").lower(),
                                                   (biweekly, 14))

    if direction in ("BULLISH", "BEARISH"):
        money_label, atr_offset = _horizon_moneyness(horizon_key, confidence)
        # Pick rounding mode that preserves the intended moneyness so a
        # "slight ITM" label can never round across spot into OTM.
        if atr_offset > 0.1:
            round_mode_call = "down"   # ITM call → strike ≤ spot
            round_mode_put  = "up"     # ITM put  → strike ≥ spot
        elif atr_offset < -0.1:
            round_mode_call = "up"     # OTM call → strike ≥ spot
            round_mode_put  = "down"   # OTM put  → strike ≤ spot
        else:
            round_mode_call = round_mode_put = "nearest"
        if direction == "BULLISH":
            action = "BUY_CALL"
            # ITM call has strike BELOW spot (intrinsic = price - strike)
            strike_raw = price - atr_offset * atr
            strike = _round_to_tick_directional(strike_raw, tick, round_mode_call)
            # Guard: strike must be sensible (positive, within 30% of spot)
            if strike <= 0 or strike < price * 0.7:
                strike = _round_to_tick(price, tick)
            target = round(price + 3 * atr, 2)
            stop = round(price - 2 * atr, 2)
            fib618 = fib.get("fib_618", 0)
            conf_boost = ("61.8% Fib support near entry. "
                          if fib618 and abs(price - fib618) / price < 0.02 else "")
            entry_trigger = (f"{conf_boost}Price clears ${round(price * 1.005, 2):.2f} "
                             f"on volume ≥ 1.3× avg")
            risk_note = (f"Close call if price breaks ${stop:.2f} (2×ATR). "
                         f"Max loss = premium paid.")
        else:  # BEARISH
            action = "BUY_PUT"
            # ITM put has strike ABOVE spot (intrinsic = strike - price)
            strike_raw = price + atr_offset * atr
            strike = _round_to_tick_directional(strike_raw, tick, round_mode_put)
            if strike <= 0 or strike > price * 1.3:
                strike = _round_to_tick(price, tick)
            target = round(price - 3 * atr, 2)
            stop = round(price + 2 * atr, 2)
            fib382 = fib.get("fib_382", 0)
            conf_boost = ("38.2% Fib resistance near entry. "
                          if fib382 and abs(price - fib382) / price < 0.02 else "")
            entry_trigger = (f"{conf_boost}Price breaks ${round(price * 0.995, 2):.2f} "
                             f"on volume ≥ 1.3× avg")
            risk_note = (f"Close put if price recovers above ${stop:.2f} (2×ATR). "
                         f"Max loss = premium paid.")

        premium_est = _estimate_premium(price, strike, atr, primary_dte, direction)
        delta_est = _estimate_delta(price, strike, atr, primary_dte, direction)
        # Breakeven: call → strike + premium; put → strike − premium.
        if direction == "BULLISH":
            breakeven = round(strike + premium_est, 2)
        else:
            breakeven = round(strike - premium_est, 2)
    else:
        action = "HOLD"
        money_label = "—"
        strike = _round_to_tick(price, tick)
        target = price
        stop = round(price - 2 * atr, 2)
        premium_est = 0.0
        delta_est = 0.0
        breakeven = price
        entry_trigger = "No clear entry — wait for 5/9 agent consensus"
        risk_note = "No position recommended. Mixed signals or choppy market."

    expiry = f"Weekly: {weekly}  |  2-Week: {biweekly}  |  Monthly: {monthly}"

    vola = ind.get("volatility_20d", 20)
    if vola > 50:
        risk_note += f" EXTREME vol ({vola:.0f}%): use spreads."
    elif vola > 35:
        risk_note += f" High vol ({vola:.0f}%): size down 50%."

    # `strike_hint` keeps its existing free-text format for back-compat
    # (older clients still parse it). New numeric fields below are what
    # the updated UI binds to.
    strike_hint_txt = f"${strike:g} strike ({money_label})" if action != "HOLD" else f"${strike:g}"

    return {
        "action": action,
        "strike": strike,                           # NEW: numeric, real listed tick
        "strike_moneyness": money_label,            # NEW: ATM / slight ITM / ITM
        "strike_premium_est": premium_est,          # NEW: $ per share at primary expiry
        "strike_delta_est": delta_est,              # NEW: ≈ Δ for primary expiry
        "strike_breakeven": breakeven,              # NEW: spot price needed at expiry
        "strike_primary_expiry": primary_exp,       # NEW: which expiry the above are for
        "strike_primary_dte": primary_dte,          # NEW
        "strike_tick": tick,                        # NEW: increment used (debug)
        "strike_hint": strike_hint_txt,
        "expiry_hint": expiry,
        "expiry_weekly": weekly,
        "expiry_biweekly": biweekly,
        "expiry_monthly": monthly,
        "entry_trigger": entry_trigger,
        "risk_note": risk_note,
        "target_price": target,
        "stop_price": stop,
    }
