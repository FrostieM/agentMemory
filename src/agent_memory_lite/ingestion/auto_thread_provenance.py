"""Auto-thread source_episode_id from recent agent activity (Move 1).

When `memory_write_decision` / `memory_write_theory` is called with
``source_episode_id=None`` and the agent has recently ingested an
episode in the same workspace, we wire the episode in automatically.

Why: agentLight 30-day audit showed 0/13 decisions with provenance —
the parameter is optional in the schema and agents reliably forget to
pass it. Making it a smart default closes the gap structurally without
yet another advisory rule.

Lookup strategy: the most recent ``audit_log`` row with
``action='ingest_episode'`` and matching ``workspace_id`` +
``agent_id`` + ``created_at >= now - WINDOW``. We use audit_log instead
of querying episodes directly because episodes lacks an ``agent_id``
column, but the audit row does (1.3.0+).

When no agent_id is set on the request (script / MCP-stdio call without
the middleware), fall back to "any recent ingest_episode in this
workspace" within a tighter 60-second window — short enough to make
accidental cross-attribution rare.

Pure helper; never raises. Returns ``None`` when no match found, in
which case the caller writes the decision/theory with NULL provenance.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.api.agent_context import current_agent_id

# Defaults — overridable via Settings.
DEFAULT_WINDOW_MINUTES = 10
ANONYMOUS_WINDOW_SECONDS = 60


def find_recent_episode_for_agent(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> str | None:
    """Return the most recent ingest_episode target_id for the current agent.

    Uses ``audit_log`` rows because that's where the (action, agent_id,
    target_id, created_at) tuple lives. ``target_id`` for an ingest_episode
    audit row is the episode's id, which is exactly what
    ``source_episode_id`` expects.
    """
    agent_id = current_agent_id()
    if agent_id:
        cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
        row = conn.execute(
            "SELECT target_id FROM audit_log "
            "WHERE workspace_id = ? AND agent_id = ? "
            "AND action = 'ingest_episode' AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 1",
            (workspace_id, agent_id, cutoff.isoformat()),
        ).fetchone()
        if row is not None and row["target_id"]:
            return str(row["target_id"])
        return None
    # Anonymous fallback: tighter window, any agent. Single-agent workspaces
    # (most common case) get free auto-threading; multi-agent workspaces are
    # better off setting X-Memory-Agent-Id explicitly.
    cutoff = datetime.now(UTC) - timedelta(seconds=ANONYMOUS_WINDOW_SECONDS)
    row = conn.execute(
        "SELECT target_id FROM audit_log "
        "WHERE workspace_id = ? AND action = 'ingest_episode' "
        "AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 1",
        (workspace_id, cutoff.isoformat()),
    ).fetchone()
    if row is not None and row["target_id"]:
        return str(row["target_id"])
    return None
