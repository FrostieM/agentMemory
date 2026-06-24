"""Supersede + correction implicit feedback (Phase 1, memory-as-brain).

A superseded decision and a corrected claim are both operator signals
that "this was wrong". They feed the outcome_score loop without the
agent having to call ``record_usage_feedback`` explicitly.

Split out of ``implicit_feedback`` to keep modules small and focused;
the public symbols are re-exported from the original module path.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.usage_feedback import record_usage_feedback

_SUPPORTED_SOURCE_TYPES = {"chunk", "decision", "theory", "insight", "capability"}


def record_implicit_supersede(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    workspace_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    """Superseded decision/theory -> -0.6 feedback on the OLD row."""
    if not settings.implicit_feedback_enabled:
        return False
    if source_type not in _SUPPORTED_SOURCE_TYPES:
        return False
    record_usage_feedback(
        conn,
        workspace_id=workspace_id,
        source_type=source_type,
        source_id=source_id,
        query="",
        usefulness=-0.6,
        notes="implicit: superseded",
        source="implicit_supersede",
    )
    return True


def record_implicit_correction(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    workspace_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    """Operator-corrected claim -> -0.8 feedback on the corrected target.

    The v1.10 correction loop already writes ``memory_candidate(kind=correction)``;
    when the operator promotes that candidate to a behavior, the underlying
    decision / theory the correction targeted should ALSO carry a negative
    outcome signal so brief and lint drop it out of "Active".
    """
    if not settings.implicit_feedback_enabled:
        return False
    if source_type not in _SUPPORTED_SOURCE_TYPES:
        return False
    record_usage_feedback(
        conn,
        workspace_id=workspace_id,
        source_type=source_type,
        source_id=source_id,
        query="",
        usefulness=-0.8,
        notes="implicit: correction",
        source="implicit_correction",
    )
    return True
