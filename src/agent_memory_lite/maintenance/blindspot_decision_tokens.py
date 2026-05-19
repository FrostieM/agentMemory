"""Active-decision token-set helper with caching.

Lifted from ``blindspot_detection`` so that module fits under the
≤150-SLOC ceiling. The function builds (or returns the cached) union
of tokens across all active decisions in the lookback window —
cached on (workspace_id, max_updated_at) so repeated brief renders
inside one mutation interval don't re-tokenize the same rows.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.ingestion.capability_suggester import _tokenize
from agent_memory_lite.maintenance.blindspot_filters import (
    DECISION_TOKENS_CACHE,
    cache_remember,
)


def decision_token_set(conn: sqlite3.Connection, *, workspace_id: str, days: int) -> set[str]:
    """Union of tokens across active decisions in the lookback window."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    try:
        max_row = conn.execute(
            "SELECT COALESCE(MAX(updated_at), '') FROM decisions "
            "WHERE workspace_id = ? AND status = 'active' "
            "AND COALESCE(valid_from, created_at) >= ?",
            (workspace_id, cutoff),
        ).fetchone()
        max_updated_at = str(max_row[0] or "")
    except sqlite3.OperationalError:
        return set()
    cache_key = (workspace_id, max_updated_at)
    cached = DECISION_TOKENS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        rows = conn.execute(
            "SELECT title, decision_text, rationale FROM decisions "
            "WHERE workspace_id = ? AND status = 'active' "
            "AND COALESCE(valid_from, created_at) >= ?",
            (workspace_id, cutoff),
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    out: set[str] = set()
    for row in rows:
        # Row may be sqlite3.Row or tuple — handle both.
        if isinstance(row, sqlite3.Row):
            parts = [row["title"], row["decision_text"], row["rationale"]]
        else:
            parts = list(row)
        text = " ".join(str(p) for p in parts if p)
        out.update(_tokenize(text))
    cache_remember(cache_key, out)
    return out
