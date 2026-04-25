"""Automatic backup + restore for the AI learning database.

Why this exists
---------------
`predictions.db` is the single source of truth for everything the agents have
learned: which predictions they made, how those predictions resolved, and the
per-agent weights derived from those resolutions. The file is intentionally
gitignored (per-machine learning that survives `git pull` without merge
conflicts), but that means a single accidental delete, disk hiccup, or fresh
clone wipes every minute of accumulated learning.

This module fixes that by:

  1. Auto-backing up the DB to `backups/predictions-YYYYMMDD-HHMMSS.db` every
     `BACKUP_INTERVAL_HOURS` (default 6h). It uses SQLite's online backup API
     so it's safe to run while the server is reading/writing the same file.
  2. Rotating backups so the disk doesn't fill up — keeps the most recent
     `BACKUP_KEEP_COUNT` (default 14) and deletes the rest.
  3. Auto-restoring on startup if the main DB is missing or empty (zero rows
     in `predictions`). The newest backup is copied into place transparently,
     so a stray `rm predictions.db` no longer costs the user their learning.
  4. Exposing a `get_status()` snapshot the dashboard renders so the user
     can SEE that the system is accumulating data and is recoverable.

Public API
----------
- restore_if_missing()      — call once before init_db() at startup
- start_backup_loop()       — call once from FastAPI lifespan
- get_status() -> dict      — for /api/learning-status
- backup_now() -> dict      — manual trigger (used by /api/learning-backup)
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DIR, "predictions.db")
BACKUP_DIR = os.path.join(_DIR, "backups")

BACKUP_INTERVAL_HOURS = 6
BACKUP_KEEP_COUNT = 14
BACKUP_PREFIX = "predictions-"
BACKUP_SUFFIX = ".db"


# ─── Internal helpers ──────────────────────────────────────────────────────
def _ensure_backup_dir() -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _list_backups_sorted_newest_first() -> list[str]:
    """Return absolute backup paths sorted by mtime, newest first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    paths = []
    for name in os.listdir(BACKUP_DIR):
        if name.startswith(BACKUP_PREFIX) and name.endswith(BACKUP_SUFFIX):
            full = os.path.join(BACKUP_DIR, name)
            if os.path.isfile(full):
                paths.append(full)
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return paths


def _db_has_data(path: str) -> bool:
    """True if the DB exists and has at least one row in `predictions`.
    Returns False for missing, empty, or unreadable files (so we trigger
    a restore in any of those cases)."""
    if not os.path.isfile(path) or os.path.getsize(path) < 1024:
        return False
    try:
        c = sqlite3.connect(path)
        try:
            row = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='predictions'"
            ).fetchone()
            if not row:
                return False
            n = c.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
            return int(n) > 0
        finally:
            c.close()
    except Exception:
        return False


# ─── Public: restore on startup ────────────────────────────────────────────
def restore_if_missing() -> dict:
    """If predictions.db is missing/empty, copy the newest backup into place.
    Returns a small status dict describing what happened.
    Safe to call before init_db() — restores BEFORE schema migrations run."""
    _ensure_backup_dir()
    if _db_has_data(DB_PATH):
        return {"action": "noop", "reason": "main DB already has data"}

    backups = _list_backups_sorted_newest_first()
    if not backups:
        return {"action": "noop", "reason": "no backups to restore from"}

    src = backups[0]
    try:
        # If a stub DB exists (zero rows but valid file), move it aside so
        # the restored copy doesn't get clobbered by a stale shm/wal pair.
        for ext in ("", "-wal", "-shm", "-journal"):
            stale = DB_PATH + ext
            if os.path.exists(stale):
                try:
                    os.remove(stale)
                except OSError:
                    pass
        shutil.copy2(src, DB_PATH)
        logger.warning(
            f"learning_backup: restored predictions.db from {os.path.basename(src)}"
        )
        return {
            "action": "restored",
            "from": os.path.basename(src),
            "size_bytes": os.path.getsize(DB_PATH),
        }
    except Exception as e:
        logger.error(f"learning_backup: restore failed: {e}")
        return {"action": "error", "error": str(e)}


# ─── Public: take a backup now ─────────────────────────────────────────────
def backup_now() -> dict:
    """Snapshot predictions.db into backups/. Uses SQLite's online backup
    API so it's safe even while the server is mid-write. Rotates after."""
    _ensure_backup_dir()
    if not os.path.isfile(DB_PATH):
        return {"ok": False, "reason": "main DB does not exist yet"}

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"{BACKUP_PREFIX}{ts}{BACKUP_SUFFIX}")
    try:
        src_conn = sqlite3.connect(DB_PATH)
        dst_conn = sqlite3.connect(dst)
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()
    except Exception as e:
        logger.error(f"learning_backup: backup failed: {e}")
        return {"ok": False, "error": str(e)}

    # Rotate — keep only the newest BACKUP_KEEP_COUNT
    removed = 0
    for old in _list_backups_sorted_newest_first()[BACKUP_KEEP_COUNT:]:
        try:
            os.remove(old)
            removed += 1
        except OSError:
            pass

    return {
        "ok": True,
        "file": os.path.basename(dst),
        "size_bytes": os.path.getsize(dst),
        "rotated": removed,
        "kept": min(BACKUP_KEEP_COUNT, len(_list_backups_sorted_newest_first())),
    }


# ─── Public: background loop ───────────────────────────────────────────────
_loop_started = False


def start_backup_loop() -> None:
    """Schedule periodic backups. Idempotent — safe to call multiple times."""
    global _loop_started
    if _loop_started:
        return
    _loop_started = True

    async def _loop():
        # Take an immediate backup on first run if none exists or the
        # newest is older than the interval. This guarantees the user has
        # at least one snapshot within minutes of first launch.
        try:
            newest = _list_backups_sorted_newest_first()
            if not newest or (time.time() - os.path.getmtime(newest[0])) > BACKUP_INTERVAL_HOURS * 3600:
                res = backup_now()
                if res.get("ok"):
                    logger.info(f"learning_backup: initial snapshot {res['file']}")
        except Exception as e:
            logger.warning(f"learning_backup: initial snapshot failed: {e}")

        while True:
            await asyncio.sleep(BACKUP_INTERVAL_HOURS * 3600)
            try:
                res = backup_now()
                if res.get("ok"):
                    logger.info(
                        f"learning_backup: periodic snapshot {res['file']} "
                        f"(kept {res['kept']}, rotated {res['rotated']})"
                    )
            except Exception as e:
                logger.warning(f"learning_backup: periodic snapshot failed: {e}")

    try:
        asyncio.get_event_loop().create_task(_loop())
    except RuntimeError:
        # No running loop — caller will schedule it from the lifespan
        pass


# ─── Public: status snapshot for the dashboard ─────────────────────────────
def get_status() -> dict:
    """Return a complete snapshot of the learning state — for /api/learning-status.
    Used by the dashboard so the user can SEE that the AI is accumulating data."""
    out: dict = {
        "db_path": DB_PATH,
        "db_exists": os.path.isfile(DB_PATH),
        "db_size_bytes": os.path.getsize(DB_PATH) if os.path.isfile(DB_PATH) else 0,
        "db_modified_iso": None,
        "predictions_total": 0,
        "predictions_resolved": 0,
        "predictions_pending": 0,
        "first_prediction_iso": None,
        "last_prediction_iso": None,
        "last_resolved_iso": None,
        "agents": [],
        "backup_dir": BACKUP_DIR,
        "backup_count": 0,
        "latest_backup": None,
        "latest_backup_iso": None,
        "latest_backup_size_bytes": 0,
        "is_learning": False,
    }
    if os.path.isfile(DB_PATH):
        out["db_modified_iso"] = datetime.fromtimestamp(
            os.path.getmtime(DB_PATH), tz=timezone.utc
        ).isoformat()
        try:
            c = sqlite3.connect(DB_PATH)
            c.row_factory = sqlite3.Row
            try:
                out["predictions_total"] = c.execute(
                    "SELECT COUNT(*) FROM predictions"
                ).fetchone()[0]
                out["predictions_resolved"] = c.execute(
                    "SELECT COUNT(*) FROM predictions WHERE outcome IS NOT NULL"
                ).fetchone()[0]
                out["predictions_pending"] = (
                    out["predictions_total"] - out["predictions_resolved"]
                )
                row = c.execute(
                    "SELECT MIN(created_at), MAX(created_at) FROM predictions"
                ).fetchone()
                if row:
                    out["first_prediction_iso"] = row[0]
                    out["last_prediction_iso"] = row[1]
                row = c.execute(
                    "SELECT MAX(outcome_checked_at) FROM predictions WHERE outcome IS NOT NULL"
                ).fetchone()
                if row:
                    out["last_resolved_iso"] = row[0]
                for r in c.execute(
                    """SELECT agent_name, weight, total_predictions, correct_predictions,
                              call_total, call_correct, put_total, put_correct, accuracy
                       FROM agent_weights ORDER BY agent_name"""
                ):
                    total = int(r["total_predictions"] or 0)
                    correct = int(r["correct_predictions"] or 0)
                    out["agents"].append({
                        "name": r["agent_name"],
                        "weight": round(float(r["weight"] or 1.0), 3),
                        "total": total,
                        "correct": correct,
                        "accuracy_pct": round((correct / total * 100), 1) if total else None,
                        "call_total": int(r["call_total"] or 0),
                        "call_correct": int(r["call_correct"] or 0),
                        "put_total": int(r["put_total"] or 0),
                        "put_correct": int(r["put_correct"] or 0),
                    })
            finally:
                c.close()
        except Exception as e:
            out["db_error"] = str(e)

    backups = _list_backups_sorted_newest_first()
    out["backup_count"] = len(backups)
    if backups:
        newest = backups[0]
        out["latest_backup"] = os.path.basename(newest)
        out["latest_backup_iso"] = datetime.fromtimestamp(
            os.path.getmtime(newest), tz=timezone.utc
        ).isoformat()
        out["latest_backup_size_bytes"] = os.path.getsize(newest)

    # "Is learning?" — true if at least one prediction landed in the DB AND
    # at least one has been resolved (otherwise we're only collecting, not
    # actually adjusting weights yet).
    out["is_learning"] = out["predictions_total"] > 0
    out["weights_adjusting"] = out["predictions_resolved"] > 0
    return out
