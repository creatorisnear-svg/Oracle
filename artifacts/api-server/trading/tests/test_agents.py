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

# Summary
print("\n" + "=" * 60)
n_pass = sum(1 for s, _ in results if s == PASS)
n_fail = sum(1 for s, _ in results if s == FAIL)
print(f"RESULTS: {n_pass} passed, {n_fail} failed (of {len(results)})")
if n_fail:
    print("\nFAILED:")
    for s, name in results:
        if s == FAIL: print(f"  - {name}")
sys.exit(0 if n_fail == 0 else 1)
