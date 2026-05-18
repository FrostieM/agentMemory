"""Derive feedback from existing operator/agent actions (v1.4 improvement).

The original v1.4 path required the agent to call
``record_usage_feedback`` explicitly. In practice it almost never does,
so the EWMA term in scoring stays at 0 and the loop is dead.

This module reads existing actions that already encode the operator's
judgement and translates them into ``memory_usage_feedback`` rows with
``source='implicit_<action>'`` so the EWMA aggregator picks them up
without changing operator habits.

Mapping (default weights, tunable via env if needed later):

    archive(chunk|decision|theory)            -> -1.0    (explicit "noisy")
    promote_candidate(target)                 -> +0.7    (explicit "useful")
    link_capability(target_id, strength=W)    -> +W      (operator weights it)
    decision_candidate.promoted (target=src) -> +0.7

Self-loop guard remains intact via the ``source`` column: implicit rows
all carry ``source='implicit_*'``, so the aggregator's
``MEMORY_FEEDBACK_EXCLUDE_SELF_LOOP`` toggle won't drop them.
The whole module is gated by ``MEMORY_IMPLICIT_FEEDBACK_ENABLED`` so a
workspace can opt in once it has confidence in the mapping.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.usage_feedback import record_usage_feedback

_SUPPORTED_SOURCE_TYPES = {"chunk", "decision", "theory", "insight", "capability"}


def record_implicit_archive(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    workspace_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    """Archived row -> -1.0 feedback (operator marked it noise)."""
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
        usefulness=-1.0,
        notes="implicit: archive",
        source="implicit_archive",
    )
    return True


def record_implicit_promote(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    workspace_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    """Promoted candidate -> +0.7 feedback for the target it generated."""
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
        usefulness=0.7,
        notes="implicit: promote",
        source="implicit_promote",
    )
    return True


def record_implicit_link(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    workspace_id: str,
    target_type: str,
    target_id: str,
    strength: float,
) -> bool:
    """Capability link strength used as direct feedback weight."""
    if not settings.implicit_feedback_enabled:
        return False
    # capability_links target the typed memory objects (theory / decision /
    # insight / experiment / etc). Map the link target_type to a feedback
    # source_type when possible; otherwise skip.
    mapped = _map_link_target_type(target_type)
    if mapped is None:
        return False
    weight = max(0.0, min(1.0, float(strength)))
    if weight <= 0.0:
        return False
    record_usage_feedback(
        conn,
        workspace_id=workspace_id,
        source_type=mapped,
        source_id=target_id,
        query="",
        usefulness=weight,
        notes=f"implicit: link strength {weight:.2f}",
        source="implicit_link",
    )
    return True


def _map_link_target_type(target_type: str) -> str | None:
    """capability_links.target_type → memory_usage_feedback.source_type."""
    mapping = {
        "theory": "theory",
        "decision": "decision",
        "research_insight": "insight",
        "memory_candidate": None,  # not in feedback enum
        "experiment": None,  # not in feedback enum
        "theory_evidence": None,
        "experiment_result": None,
    }
    return mapping.get(target_type)


# ============================================================
# Phase 1 (memory-as-organ): supersede + correction implicit feedback.
# A superseded decision and a corrected claim are both operator signals
# that "this was wrong". They feed the outcome_score loop without the
# agent having to call record_usage_feedback explicitly.
# ============================================================


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
