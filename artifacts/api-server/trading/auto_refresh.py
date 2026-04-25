"""Background scheduler that keeps the data files fresh without manual scripts.

Why this exists
---------------
`track_record.json` and `regime_stats.json` describe the model's empirical
hit rate per stock and per volatility regime — they're the "memory" the model
uses to honestly calibrate its target-hit probabilities.

These files are now LOCAL-ONLY (gitignored) so they survive `git pull`
without merge conflicts and accumulate per-machine learning. On a fresh
clone they don't exist — this module rebuilds them automatically on first
server startup, then refreshes them every 7 days in the background.

Public API
----------
- start_background_loop()  — call once from the FastAPI lifespan
- get_status()             — returns ages + last-refresh timestamps
- refresh_track_record()   — async, idempotent (used by /api/admin route)
- refresh_regime_stats()   — async, idempotent (used by /api/admin route)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
TRACK_RECORD_PATH = os.path.join(_DIR, "track_record.json")
REGIME_STATS_PATH = os.path.join(_DIR, "regime_stats.json")

# How stale a file can get before we re-derive it.
MAX_AGE_DAYS = 7
# How often the loop wakes up to check.
LOOP_INTERVAL_HOURS = 24

# In-flight locks so two refreshes never run in parallel for the same file.
_LOCKS: dict[str, asyncio.Lock] = {}

# Last-refresh metadata for the status endpoint.
_LAST_RESULT: dict[str, dict[str, Any]] = {
    "track_record": {"started": None, "finished": None, "ok": None, "error": None},
    "regime_stats": {"started": None, "finished": None, "ok": None, "error": None},
}


def _file_age_days(path: str) -> float:
    if not os.path.exists(path):
        return float("inf")
    return (time.time() - os.path.getmtime(path)) / 86400.0


def _needs_refresh(path: str) -> bool:
    return _file_age_days(path) > MAX_AGE_DAYS


def _lock_for(name: str) -> asyncio.Lock:
    if name not in _LOCKS:
        _LOCKS[name] = asyncio.Lock()
    return _LOCKS[name]


# ─── Core sync regenerators (run in a worker thread) ─────────────────────
def _regen_track_record_sync() -> dict:
    """Heavy: ~1-2 minutes. Runs in thread executor."""
    # Defer imports so this module can be loaded before agents.py if needed.
    from agents import (
        PriceActionAgent, TechnicalAgent, VolumeAgent, SentimentAgent,
        OptionsFlowAgent, MomentumAgent, RiskAgent, FearGreedAgent,
        PoliticalAgent, JudgeAgent,
    )
    from tests.compute_track_record import evaluate, DEFAULT_SYMBOLS

    agents = [
        PriceActionAgent(), TechnicalAgent(), VolumeAgent(), SentimentAgent(),
        OptionsFlowAgent(), MomentumAgent(), RiskAgent(), FearGreedAgent(),
        PoliticalAgent(),
    ]
    judge = JudgeAgent()

    out: dict[str, Any] = {"per_stock": {}, "overall": {}}
    total_w = total_l = 0
    for sym in DEFAULT_SYMBOLS:
        try:
            r = evaluate(sym, agents, judge)
            out["per_stock"][sym] = r
            total_w += r.get("wins", 0)
            total_l += r.get("losses", 0)
            hr = r.get("hit_rate")
            logger.info(f"  track-record {sym:6s}  signals={r.get('signals',0)}  hit_rate={hr}")
        except Exception as e:
            out["per_stock"][sym] = {"signals": 0, "hit_rate": None, "error": str(e)}
            logger.warning(f"  track-record {sym} FAILED: {e}")

    n = total_w + total_l
    out["overall"] = {
        "signals": n, "wins": total_w, "losses": total_l,
        "hit_rate": round(total_w / n * 100, 1) if n else None,
    }
    out["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    with open(TRACK_RECORD_PATH, "w") as f:
        json.dump(out, f, indent=2)
    return out


def _regen_regime_stats_sync() -> dict:
    """Heavy: ~1-2 minutes. Runs in thread executor."""
    from tests.compute_regime_stats import collect, summarize

    buckets = collect()
    stats = summarize(buckets)
    stats["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    with open(REGIME_STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)
    return stats


# ─── Async wrappers + cache reloads ──────────────────────────────────────
async def refresh_track_record(force: bool = False) -> dict:
    """Refresh track_record.json if missing/stale (or force=True). Hot-reloads cache."""
    async with _lock_for("track_record"):
        if not force and not _needs_refresh(TRACK_RECORD_PATH):
            age = _file_age_days(TRACK_RECORD_PATH)
            return {"skipped": True, "age_days": round(age, 2), "path": TRACK_RECORD_PATH}

        meta = _LAST_RESULT["track_record"]
        meta["started"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        started = time.time()
        logger.info("auto-refresh: regenerating track_record.json (this takes ~1-2 minutes)…")
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, _regen_track_record_sync)
            elapsed = time.time() - started
            # Hot-reload the in-memory cache in agents.py
            try:
                import agents as _agents_mod
                if hasattr(_agents_mod, "reload_track_record"):
                    _agents_mod.reload_track_record()
            except Exception as e:
                logger.warning(f"track_record cache reload failed: {e}")
            meta["finished"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            meta["ok"] = True
            meta["error"] = None
            meta["elapsed_sec"] = round(elapsed, 1)
            logger.info(f"auto-refresh: track_record.json done in {elapsed:.0f}s "
                        f"(overall hit-rate {result.get('overall', {}).get('hit_rate')}%)")
            return {"ok": True, "elapsed_sec": round(elapsed, 1), "overall": result.get("overall")}
        except Exception as e:
            meta["finished"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            meta["ok"] = False
            meta["error"] = str(e)
            logger.error(f"auto-refresh: track_record FAILED: {e}")
            return {"ok": False, "error": str(e)}


async def refresh_regime_stats(force: bool = False) -> dict:
    """Refresh regime_stats.json if missing/stale (or force=True). Hot-reloads cache."""
    async with _lock_for("regime_stats"):
        if not force and not _needs_refresh(REGIME_STATS_PATH):
            age = _file_age_days(REGIME_STATS_PATH)
            return {"skipped": True, "age_days": round(age, 2), "path": REGIME_STATS_PATH}

        meta = _LAST_RESULT["regime_stats"]
        meta["started"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        started = time.time()
        logger.info("auto-refresh: regenerating regime_stats.json (this takes ~1-2 minutes)…")
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, _regen_regime_stats_sync)
            elapsed = time.time() - started
            # Hot-reload the in-memory cache in kelly.py
            try:
                import kelly as _kelly_mod
                if hasattr(_kelly_mod, "reload_stats"):
                    _kelly_mod.reload_stats()
            except Exception as e:
                logger.warning(f"regime_stats cache reload failed: {e}")
            meta["finished"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            meta["ok"] = True
            meta["error"] = None
            meta["elapsed_sec"] = round(elapsed, 1)
            logger.info(f"auto-refresh: regime_stats.json done in {elapsed:.0f}s")
            return {"ok": True, "elapsed_sec": round(elapsed, 1), "stats": result}
        except Exception as e:
            meta["finished"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            meta["ok"] = False
            meta["error"] = str(e)
            logger.error(f"auto-refresh: regime_stats FAILED: {e}")
            return {"ok": False, "error": str(e)}


def get_status() -> dict:
    """Lightweight status for an admin endpoint / dashboard freshness display."""
    return {
        "max_age_days": MAX_AGE_DAYS,
        "loop_interval_hours": LOOP_INTERVAL_HOURS,
        "track_record": {
            "path": TRACK_RECORD_PATH,
            "exists": os.path.exists(TRACK_RECORD_PATH),
            "age_days": round(_file_age_days(TRACK_RECORD_PATH), 2)
                if os.path.exists(TRACK_RECORD_PATH) else None,
            "stale": _needs_refresh(TRACK_RECORD_PATH),
            "last_refresh": _LAST_RESULT["track_record"],
        },
        "regime_stats": {
            "path": REGIME_STATS_PATH,
            "exists": os.path.exists(REGIME_STATS_PATH),
            "age_days": round(_file_age_days(REGIME_STATS_PATH), 2)
                if os.path.exists(REGIME_STATS_PATH) else None,
            "stale": _needs_refresh(REGIME_STATS_PATH),
            "last_refresh": _LAST_RESULT["regime_stats"],
        },
    }


async def _background_loop():
    """Forever loop. On boot: refresh stale files. Then repeat every LOOP_INTERVAL_HOURS."""
    # Stagger startup so the first analysis request isn't competing for yfinance.
    await asyncio.sleep(5)
    while True:
        try:
            # Run sequentially — both touch yfinance and use ~1 CPU core each.
            await refresh_regime_stats()
            await refresh_track_record()
        except Exception as e:
            logger.error(f"auto-refresh loop tick error: {e}")
        await asyncio.sleep(LOOP_INTERVAL_HOURS * 3600)


def start_background_loop() -> asyncio.Task:
    """Schedule the auto-refresh loop on the current event loop."""
    return asyncio.create_task(_background_loop())
