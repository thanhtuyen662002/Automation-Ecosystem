"""
State Persistence — atomic JSON serialization for all in-memory singletons.

Solves the critical gap: daily caps, cooldown timers, and session timestamps
are lost when the worker process restarts. This module snapshots all three
singletons to disk on every session_end and restores them on startup.

Design decisions:
  - JSON format (human-readable, debuggable)
  - Atomic write: write to .tmp then os.replace() — safe on Windows + POSIX
  - Single file: ./data/runtime_state.json
  - First-wins: existing in-memory state is never overwritten on restore
    (if a session started before restore completed, don't clobber it)

Usage:
    # In worker startup (before any session):
    from core.persistence import restore_state
    restore_state()

    # In session_planner.record_session_end() or worker shutdown:
    from core.persistence import save_state
    save_state()
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("core.persistence")

# Default state file location — override via AUTOMATION_STATE_FILE env var
_DEFAULT_STATE_FILE = Path("data") / "runtime_state.json"


def _state_path() -> Path:
    env = os.environ.get("AUTOMATION_STATE_FILE")
    return Path(env) if env else _DEFAULT_STATE_FILE


# ── Save ──────────────────────────────────────────────────────────────────────

def save_state(path: Path | None = None) -> None:
    """Snapshot all in-memory singleton state to disk atomically.

    Writes to <path>.tmp first, then renames to <path>.
    Safe to call from any thread or coroutine — no locks needed because
    os.replace() is atomic on all supported platforms.

    Call this:
      - After every session_end (in session_planner.record_session_end)
      - On graceful worker shutdown
    """
    target = path or _state_path()

    # Import singletons lazily to avoid circular imports at module load time
    from core.account_brain import get_brain_registry
    from core.cross_account_coordinator import get_coordinator
    from core.lifecycle_manager import get_lifecycle_manager

    registry  = get_brain_registry()
    lifecycle = get_lifecycle_manager()
    coord     = get_coordinator()

    state: dict[str, Any] = {
        "saved_at": time.time(),
        "version":  1,
        # AccountBrainRegistry: all per-account brain states
        "brain": registry.dump_states() if hasattr(registry, "dump_states") else {},
        # LifecycleManager: all per-account lifecycle states
        "lifecycle": lifecycle.dump_states(),
        # CrossAccountCoordinator: daily session + upload counters per account
        # (rolling rate-limit windows are ephemeral — not worth persisting)
        "coordinator_daily": _dump_coordinator_daily(coord),
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        LOGGER.info("persistence_saved", extra={
            "event":          "persistence_saved",
            "path":           str(target),
            "brain_accounts": len(state["brain"]),
            "lifecycle_accounts": len(state["lifecycle"]),
        })
    except Exception as exc:
        LOGGER.error("persistence_save_failed", extra={
            "event": "persistence_save_failed",
            "path":  str(target),
            "error": str(exc),
        })
        # Non-fatal: in-memory state is still correct; next save will retry


# ── Restore ───────────────────────────────────────────────────────────────────

def restore_state(path: Path | None = None) -> bool:
    """Restore singleton state from disk on worker startup.

    Returns True if state was successfully loaded, False if no file found
    or file is corrupt (non-fatal — starts fresh).

    Call this ONCE at worker startup, before any session begins.
    Uses first-wins semantics: if the singleton already has state for an
    account (e.g. from a concurrent process), it is not overwritten.
    """
    target = path or _state_path()

    if not target.exists():
        LOGGER.info("persistence_no_file", extra={
            "event": "persistence_no_file",
            "path":  str(target),
        })
        return False

    try:
        raw = target.read_text(encoding="utf-8")
        state: dict[str, Any] = json.loads(raw)
    except Exception as exc:
        LOGGER.error("persistence_load_failed", extra={
            "event": "persistence_load_failed",
            "path":  str(target),
            "error": str(exc),
        })
        return False

    version = state.get("version", 0)
    saved_at = state.get("saved_at", 0.0)
    age_hours = (time.time() - saved_at) / 3600

    # Safety: don't restore state older than 25 hours (stale daily counters)
    if age_hours > 25.0:
        LOGGER.warning("persistence_state_stale", extra={
            "event":     "persistence_state_stale",
            "age_hours": round(age_hours, 1),
            "action":    "skipping_restore",
        })
        return False

    from core.account_brain import get_brain_registry
    from core.cross_account_coordinator import get_coordinator
    from core.lifecycle_manager import get_lifecycle_manager

    registry  = get_brain_registry()
    lifecycle = get_lifecycle_manager()
    coord     = get_coordinator()

    # Restore brain states
    brain_data: dict[str, Any] = state.get("brain", {})
    if hasattr(registry, "load_states") and brain_data:
        registry.load_states(brain_data)

    # Restore lifecycle states
    lc_data: dict[str, Any] = state.get("lifecycle", {})
    if lc_data:
        lifecycle.load_states(lc_data)

    # Restore coordinator daily counters
    _restore_coordinator_daily(coord, state.get("coordinator_daily", {}))

    LOGGER.info("persistence_restored", extra={
        "event":              "persistence_restored",
        "path":               str(target),
        "version":            version,
        "age_hours":          round(age_hours, 2),
        "brain_accounts":     len(brain_data),
        "lifecycle_accounts": len(lc_data),
    })
    return True


# ── Coordinator daily counter helpers ─────────────────────────────────────────
# The coordinator's daily counters are dict[str, int|str] held in private attrs.
# We access them via the public interface to stay decoupled.

def _dump_coordinator_daily(coord: Any) -> dict[str, Any]:
    """Extract per-account daily session + upload counters from coordinator."""
    try:
        return {
            "session_date":  dict(getattr(coord, "_account_session_date", {})),
            "sessions":      dict(getattr(coord, "_account_sessions", {})),
            "upload_date":   dict(getattr(coord, "_account_upload_date", {})),
            "uploads":       dict(getattr(coord, "_account_uploads", {})),
        }
    except Exception as exc:
        LOGGER.warning("persistence_coord_dump_failed", extra={"error": str(exc)})
        return {}


def _restore_coordinator_daily(coord: Any, data: dict[str, Any]) -> None:
    """Restore per-account daily counters into coordinator (first-wins)."""
    if not data:
        return
    try:
        today = coord._today_utc() if hasattr(coord, "_today_utc") else ""

        session_date: dict = data.get("session_date", {})
        sessions: dict     = data.get("sessions", {})
        upload_date: dict  = data.get("upload_date", {})
        uploads: dict      = data.get("uploads", {})

        for account_id, date_str in session_date.items():
            if account_id not in getattr(coord, "_account_session_date", {}):
                if date_str == today:  # Only restore if counters are for today
                    coord._account_session_date[account_id] = date_str
                    coord._account_sessions[account_id] = int(sessions.get(account_id, 0))

        for account_id, date_str in upload_date.items():
            if account_id not in getattr(coord, "_account_upload_date", {}):
                if date_str == today:
                    coord._account_upload_date[account_id] = date_str
                    coord._account_uploads[account_id] = int(uploads.get(account_id, 0))

    except Exception as exc:
        LOGGER.warning("persistence_coord_restore_failed", extra={"error": str(exc)})


# ── Convenience: auto-save after session end ──────────────────────────────────

def save_after_session(account_id: str) -> None:
    """Lightweight wrapper to trigger save_state after a session completes.

    Logs the triggering account for audit purposes.
    Errors are non-fatal.
    """
    try:
        save_state()
    except Exception as exc:
        LOGGER.error("persistence_auto_save_failed", extra={
            "event":      "persistence_auto_save_failed",
            "account_id": account_id,
            "error":      str(exc),
        })
