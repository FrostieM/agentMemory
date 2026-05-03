"""Per-workspace in-flight locks for the sentinel scheduler.

Extracted into its own module to keep ``sentinel_scheduler.py`` under the
150 SLOC ceiling. The locks are process-local; multi-process safety is
deferred (the typical deployment is one HTTP service per machine).

Usage:

    lock = get_workspace_lock(workspace_id)
    if not lock.acquire(blocking=False):
        return  # in-flight elsewhere
    try:
        ...                                # work
    finally:
        lock.release()
"""

from __future__ import annotations

import threading

_workspace_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def get_workspace_lock(workspace_id: str) -> threading.Lock:
    """Return (and lazily create) the lock for a given workspace."""
    with _guard:
        lock = _workspace_locks.get(workspace_id)
        if lock is None:
            lock = threading.Lock()
            _workspace_locks[workspace_id] = lock
        return lock


def reset_for_tests() -> None:
    """Drop every cached lock. Called by unit tests so cross-test state
    cannot leak (e.g. one test holds a lock, the next sees it stuck)."""
    with _guard:
        _workspace_locks.clear()
