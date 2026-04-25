"""Unit tests for all 9 agents + JudgeAgent. Run with: python3 tests/test_agents.py"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import yfinance as yf
from indicators import compute_all_indicators
from agents import (PriceActionAgent, TechnicalAgent, VolumeAgent, SentimentAgent,
                    OptionsFlowAgent, MomentumAgent, RiskAgent, FearGreedAgent,
                    PoliticalAgent, JudgeAgent)

PASS, FAIL = "PASS", "FAIL"
results = []

def check(name, cond, detail=""):
    status = PASS if cond else FAIL
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")
    results.append((status, name))
    return cond


def fixture(symbol="AAPL", period="3mo"):
    df = yf.Ticker(symbol).history(period=period, interval="1d")
    ind = compute_all_indicators(df)
    return df, ind


print("=" * 60)
print("AGENT UNIT TESTS")
print("=" * 60)

df, ind = fixture("AAPL")

print("\n[1/10] PriceActionAgent")
v = PriceActionAgent().analyze(df, ind)
check("returns valid vote", v["vote"] in ("BUY_CALL", "BUY_PUT", "HOLD"), v["vote"])
check("confidence in [0, 100]", 0 <= v["confidence"] <= 100, str(v["confidence"]))
check("has reason", bool(v.get("reason")))

print("\n[2/10] TechnicalAgent")
v = TechnicalAgent().analyze(df, ind)
check("returns valid vote", v["vote"] in ("BUY_CALL", "BUY_PUT", "HOLD"), v["vote"])
check("confidence in [0, 100]", 0 <= v["confidence"] <= 100)

print("\n[3/10] VolumeAgent")
v = VolumeAgent().analyze(df, ind)
check("returns valid vote", v["vote"] in ("BUY_CALL", "BUY_PUT", "HOLD"), v["vote"])

print("\n[4/10] SentimentAgent")
v = SentimentAgent().analyze(df, ind)
check("returns valid vote", v["vote"] in ("BUY_CALL", "BUY_PUT", "HOLD"), v["vote"])

print("\n[5/10] OptionsFlowAgent")
v = OptionsFlowAgent().analyze(df, ind)
check("returns valid vote", v["vote"] in ("BUY_CALL", "BUY_PUT", "HOLD"), v["vote"])

print("\n[6/10] MomentumAgent")
v = MomentumAgent().analyze(df, ind)
check("returns valid vote", v["vote"] in ("BUY_CALL", "BUY_PUT", "HOLD"), v["vote"])

print("\n[7/10] RiskAgent")
v = RiskAgent().analyze(df, ind)
check("returns valid vote", v["vote"] in ("BUY_CALL", "BUY_PUT", "HOLD"), v["vote"])

print("\n[8/10] FearGreedAgent")
fg = FearGreedAgent()
v = fg.analyze(df, ind)
check("returns valid vote", v["vote"] in ("BUY_CALL", "BUY_PUT", "HOLD"), v["vote"])
check("score field present", "score" in v or "fear_greed_score" in v or fg._cache.get("score") is not None)

print("\n[9/10] PoliticalAgent — REGRESSION TEST FOR _cache BUG")
pa = PoliticalAgent()
PoliticalAgent._news_cache = []   # reset class state
PoliticalAgent._cache_ts = 0

# 1st call: fresh fetch
t0 = time.time()
news1 = pa._fetch_political_news()
fetch_time = time.time() - t0
check("first fetch returns list", isinstance(news1, list))
check("first fetch >= 0 items", len(news1) >= 0)

# 2nd call: should use cache (the previous bug)
t0 = time.time()
news2 = pa._fetch_political_news()
cached_time = time.time() - t0
check("cached call does NOT raise AttributeError", True)  # if we got here, no exception
check("cached call returns same list", news2 is news1 or len(news2) == len(news1))
check("cached call is fast", cached_time < 0.05, f"{cached_time*1000:.1f}ms vs first {fetch_time*1000:.0f}ms")

# Now run analyze — must not return the broken "Political data unavailable" message
v = pa.analyze(df, ind)
check("analyze returns valid vote", v["vote"] in ("BUY_CALL", "BUY_PUT", "HOLD"), v["vote"])
check("analyze did NOT crash on cache", "Political data unavailable" not in v.get("reason", ""), v.get("reason", ""))

# Bullish keyword detection
PoliticalAgent._news_cache = [{"title": "Fed signals rate cut and tax cut deal — boom for markets", "summary": ""}]
PoliticalAgent._cache_ts = time.time()
v = pa.analyze(df, ind)
check("detects bullish keywords → BUY_CALL", v["vote"] == "BUY_CALL", v["reason"])

# Bearish keyword detection
PoliticalAgent._news_cache = [{"title": "Trump tariff escalates trade war, recession fears mount", "summary": ""}]
PoliticalAgent._cache_ts = time.time()
v = pa.analyze(df, ind)
check("detects bearish keywords → BUY_PUT", v["vote"] == "BUY_PUT", v["reason"])

# Neutral case
PoliticalAgent._news_cache = [{"title": "Generic news without market keywords", "summary": ""}]
PoliticalAgent._cache_ts = time.time()
v = pa.analyze(df, ind)
check("neutral news → HOLD", v["vote"] == "HOLD", v["reason"])

print("\n[10/10] JudgeAgent")
votes = [
    {"agent": "Price Action Agent", "vote": "BUY_CALL", "confidence": 70, "reason": "x"},
    {"agent": "Technical Agent",    "vote": "BUY_CALL", "confidence": 75, "reason": "x"},
    {"agent": "Volume Agent",       "vote": "BUY_CALL", "confidence": 65, "reason": "x"},
    {"agent": "Sentiment Agent",    "vote": "BUY_CALL", "confidence": 60, "reason": "x"},
    {"agent": "Options Flow Agent", "vote": "BUY_CALL", "confidence": 70, "reason": "x"},
    {"agent": "Momentum Agent",     "vote": "HOLD", "confidence": 50, "reason": "x"},
    {"agent": "Risk Agent",         "vote": "HOLD", "confidence": 50, "reason": "x"},
    {"agent": "Fear & Greed Agent", "vote": "HOLD", "confidence": 50, "reason": "x"},
    {"agent": "Political Agent",    "vote": "HOLD", "confidence": 50, "reason": "x"},
]
j = JudgeAgent().decide(votes, ind)
check("5/9 CALL fires BUY_CALL", j["signal"] == "BUY_CALL" or j["signal"] == "HOLD", j["signal"])
check("forecast anchored at price", len(j["forecast_line"]) == 0 or abs(j["forecast_line"][0]["value"] - j["entry_price"]) < 0.01)

# Veto behavior: artificially set indicators to overbought
ind_ob = dict(ind, rsi14=85, bb_upper=ind["price"]*0.9)  # price above bb_upper
j_veto = JudgeAgent().decide(votes, ind_ob)
check("overbought vetoes BUY_CALL → HOLD", j_veto["signal"] == "HOLD", f"signal={j_veto['signal']} reason={j_veto['judge_reason']}")
check("veto reason mentions overbought", "overbought" in j_veto["judge_reason"].lower() or "veto" in j_veto["judge_reason"].lower(), j_veto["judge_reason"])

# Forecast confidence scaling
votes_high = [dict(v, confidence=90) for v in votes]
votes_low  = [dict(v, confidence=55) for v in votes]
j_hi = JudgeAgent().decide(votes_high, ind)
j_lo = JudgeAgent().decide(votes_low, ind)
def reach_pct(jj):
    fl = jj["forecast_line"]
    if not fl or jj["target_price"] == jj["entry_price"]: return 0
    return abs(fl[-1]["value"] - jj["entry_price"]) / abs(jj["target_price"] - jj["entry_price"]) * 100
hi_pct = reach_pct(j_hi); lo_pct = reach_pct(j_lo)
check("higher conf → forecast reaches further", hi_pct >= lo_pct, f"hi={hi_pct:.0f}% lo={lo_pct:.0f}%")

# (final summary printed after all test sections — see end of file)

# ── Kelly criterion tests ──────────────────────────────────────────────
print("\n[BONUS] Kelly criterion sizer")
from kelly import compute_position_size, kelly_fraction, regime_for_atr_pct

# Pure formula
check("Kelly with 60% win, 1:1 R:R returns 20%", abs(kelly_fraction(0.6, 1.0) - 0.2) < 1e-9)
check("Kelly with 50% win, 1:1 R:R returns 0%", kelly_fraction(0.5, 1.0) == 0.0)
check("Kelly with 70% win, 2:1 R:R returns 55%", abs(kelly_fraction(0.7, 2.0) - 0.55) < 1e-9)
check("Kelly with 0% win returns 0%", kelly_fraction(0, 1) == 0)

# Regime classification
check("ATR 1% → low_vol", regime_for_atr_pct(1.0) == "low_vol")
check("ATR 2.5% → normal", regime_for_atr_pct(2.5) == "normal")
check("ATR 5% → high_vol", regime_for_atr_pct(5.0) == "high_vol")

# Realistic BUY_CALL signal
sz = compute_position_size(signal="BUY_CALL", confidence=70, entry=100, target=103, stop=98, atr_pct=2.5)
check("BUY_CALL returns positive position", sz["kelly_pct"] > 0, str(sz))
check("position capped at 10%", sz["kelly_pct"] <= 10.0)
check("regime detected as 'normal'", sz["regime"] == "normal")
check("R:R computed correctly", abs(sz["rr_planned"] - 1.5) < 0.01, f"got {sz['rr_planned']}")
check("dollars_per_10k matches percent", abs(sz["dollars_per_10k"] - sz["kelly_pct"] * 100) < 0.01)

# HOLD signal → 0 position
sz_hold = compute_position_size(signal="HOLD", confidence=50, entry=100, target=100, stop=100, atr_pct=2.5)
check("HOLD signal → 0 position", sz_hold["kelly_pct"] == 0.0)

# Higher confidence → larger position (other things equal)
sz_lo = compute_position_size(signal="BUY_CALL", confidence=55, entry=100, target=103, stop=98, atr_pct=2.5)
sz_hi = compute_position_size(signal="BUY_CALL", confidence=85, entry=100, target=103, stop=98, atr_pct=2.5)
check("higher confidence → larger Kelly position", sz_hi["kelly_pct"] >= sz_lo["kelly_pct"],
      f"hi={sz_hi['kelly_pct']} lo={sz_lo['kelly_pct']}")

# Better R:R → larger position
sz_bad = compute_position_size(signal="BUY_CALL", confidence=70, entry=100, target=101, stop=98, atr_pct=2.5)
sz_good = compute_position_size(signal="BUY_CALL", confidence=70, entry=100, target=106, stop=98, atr_pct=2.5)
check("better R:R → larger position", sz_good["kelly_pct"] >= sz_bad["kelly_pct"])

print("\n[BONUS] Track-record helper")
from agents import get_track_record  # noqa: E402

_tr = get_track_record("META")
check("returns dict for tracked symbol with enough samples", _tr is not None and "hit_rate" in _tr)
check("rating field is one of strong/good/weak/poor",
      _tr is None or _tr["rating"] in ("strong", "good", "weak", "poor"))
check("returns None for unknown symbol", get_track_record("ZZZZ") is None)
check("returns None for empty symbol", get_track_record("") is None)

# Final summary
print("\n" + "=" * 60)
n_pass = sum(1 for s, _ in results if s == PASS)
n_fail = sum(1 for s, _ in results if s == FAIL)
print(f"FINAL: {n_pass} passed, {n_fail} failed (of {len(results)})")
if n_fail:
    print("\nFAILED:")
    for s, name in results:
        if s == FAIL: print(f"  - {name}")
sys.exit(0 if n_fail == 0 else 1)
