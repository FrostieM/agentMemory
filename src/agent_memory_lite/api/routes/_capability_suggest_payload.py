"""Move 3 of v2.2 — wire-payload helper for capability suggestions.

Both /memory/write_decision and /memory/record_with_evidence return a
``capability_suggestions`` field listing the top-3 ranked candidates.
This module owns the conversion from ``CapabilitySuggestion`` (the
helper module's dataclass) into ``CapabilitySuggestionPayload`` (the
pydantic wire type) so neither route file ends up duplicating it.

Lifted out of ``api/routes/decisions.py`` to keep both routes under
the ≤150-SLOC ceiling — the conversion is mechanical and shared.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.schemas.decisions import CapabilitySuggestionPayload
from agent_memory_lite.ingestion.capability_suggester import (
    suggest_capabilities_for_decision,
)


def capability_suggestion_payloads(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    title: str,
    text: str,
    rationale: str | None = None,
    limit: int = 3,
) -> list[CapabilitySuggestionPayload]:
    """Return the top-N capability suggestions as wire payloads."""
    return [
        CapabilitySuggestionPayload(
            capability_type=s.capability_type,
            capability_id=s.capability_id,
            capability_name=s.capability_name,
            score=s.score,
            snippet=s.snippet,
        )
        for s in suggest_capabilities_for_decision(
            conn,
            workspace_id=workspace_id,
            title=title,
            text=text,
            rationale=rationale,
            limit=limit,
        )
    ]
