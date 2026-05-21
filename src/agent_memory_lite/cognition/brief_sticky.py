"""Sticky-brief session bookkeeping.

A per-process record of which (workspace, session) pairs have already
received a full brief — used to decide whether to cap ``max_tokens`` on
subsequent emits within the same session. Extracted from
cognition/brief.py during the v3.7 SLOC decomposition.
"""

from __future__ import annotations

import os
import threading

# Sticky-brief: per-process record of which (workspace, session) pairs
# have already received a full brief. Used to decide whether to cap
# ``max_tokens`` on subsequent emits within the same session.
# Round-2 audit: HTTP handlers run on a thread-pool so two requests
# for the same session can race on the "membership check + add"
# sequence — the sticky-tax optimisation then misfires (both emit
# full-budget). Lock turns the check+add into one atomic step.
_session_seen: set[tuple[str, str]] = set()
_SESSION_SEEN_LOCK = threading.Lock()


def _sticky_brief_enabled() -> bool:
    raw = os.environ.get("MEMORY_STICKY_BRIEF_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _sticky_followup_tokens(default: int = 200) -> int:
    raw = os.environ.get("MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS", str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(100, value)  # floor matches the HTTP brief endpoint min


def reset_session_seen() -> None:
    """Test hook: clear the sticky-brief seen-sessions set."""
    with _SESSION_SEEN_LOCK:
        _session_seen.clear()
