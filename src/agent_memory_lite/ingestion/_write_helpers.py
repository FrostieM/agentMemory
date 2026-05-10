"""Shared composition helpers for decision/theory write paths.

The HTTP route, the MCP stdio handler, and the in-process MCP tool
handler all need the same Move 1 (auto-thread source_episode_id) and
Move 3/4 (top-N capability suggestions) logic on the way out. Without
this module they were copy-pasted three times (or, worse, missing on
the MCP local-fallback path so MCP-only deployments silently lost
both features when the HTTP service was down).

The helpers take plain types (str / bool / dict) so any layer can use
them without dragging Pydantic schemas across boundaries. The wire
layer wraps the dict result in its own response model when needed.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from typing import Any

from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.ingestion.auto_thread_provenance import (
    find_recent_episode_for_agent,
)
from agent_memory_lite.ingestion.capability_suggester import (
    suggest_capabilities_for_decision,
)


def resolve_source_episode_id(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    explicit: str | None,
    allow_orphan: bool,
    settings: Settings | None = None,
) -> str | None:
    """Move 1: pick source_episode_id for a decision/theory write.

    Order of precedence:
        1. ``explicit`` value from the caller (always wins).
        2. ``None`` if ``allow_orphan`` is set (deliberate untraced write).
        3. ``None`` if ``settings.auto_thread_decision_source`` is off.
        4. The most recent ``ingest_episode`` audit row for this agent
           in ``workspace_id`` within the configured window, otherwise
           ``None``.
    """
    if explicit is not None:
        return explicit
    if allow_orphan:
        return None
    effective = settings if settings is not None else get_settings()
    if not effective.auto_thread_decision_source:
        return None
    return find_recent_episode_for_agent(conn, workspace_id=workspace_id)


def capability_suggestion_dicts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    title: str,
    text: str,
    rationale: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Move 3/4: top-N capability suggestions as plain wire-shape dicts.

    The HTTP route wraps each dict in ``CapabilitySuggestionPayload``
    for response-model validation; the MCP path returns the dicts
    verbatim. Either way the on-the-wire shape is identical so a
    downstream agent (Claude Code, /ui/review, or a script) sees the
    same field names regardless of whether HTTP or MCP transported it.
    """
    return [
        dataclasses.asdict(s)
        for s in suggest_capabilities_for_decision(
            conn,
            workspace_id=workspace_id,
            title=title,
            text=text,
            rationale=rationale,
            limit=limit,
        )
    ]
