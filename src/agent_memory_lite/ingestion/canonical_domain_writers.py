"""Business writers used by the compact canonical write surface."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion._write_helpers import (
    capability_suggestion_dicts,
    decision_neighbor_dicts,
    record_supersede_feedback,
    resolve_source_episode_id,
)
from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.theories import TheoryIn
from agent_memory_lite.storage.reader import get_object


def write_decision_canonical(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    payload: dict[str, Any],
    settings: Settings | None,
) -> dict[str, Any]:
    allow_orphan = bool(payload.pop("allow_orphan", False))
    resolved_source = resolve_source_episode_id(
        conn,
        workspace_id=workspace_id,
        explicit=payload.get("source_episode_id"),
        allow_orphan=allow_orphan,
        settings=settings,
    )
    payload["source_episode_id"] = resolved_source
    decision = write_decision(conn, DecisionIn(**payload), settings=settings)
    record_supersede_feedback(
        conn,
        workspace_id=decision.workspace_id,
        source_type="decision",
        superseded_id=decision.supersedes_decision_id,
        settings=settings,
    )
    out = get_object(conn, workspace_id=workspace_id, kind="decision", object_id=decision.id) or {
        "id": decision.id,
        "kind": "decision",
    }
    return {
        **out,
        "decision_id": decision.id,
        "status": decision.status.value,
        "valid_from": decision.valid_from,
        "superseded_decision_id": decision.supersedes_decision_id,
        "source_episode_id": decision.source_episode_id,
        "capability_suggestions": capability_suggestion_dicts(
            conn,
            workspace_id=workspace_id,
            title=decision.title,
            text=decision.decision_text,
            rationale=decision.rationale,
        ),
        "decision_neighbors": decision_neighbor_dicts(
            conn,
            workspace_id=workspace_id,
            title=decision.title,
            text=decision.decision_text,
            rationale=decision.rationale,
            exclude_id=decision.id,
        ),
    }


def write_behavior_canonical(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    instruction = upsert_behavior_instruction(conn, BehaviorInstructionIn(**payload))
    out = get_object(
        conn, workspace_id=workspace_id, kind="behavior", object_id=instruction.id
    ) or {
        "id": instruction.id,
        "kind": "behavior",
    }
    return {
        **out,
        "instruction_id": instruction.id,
        "created_at": instruction.created_at,
        "updated_at": instruction.updated_at,
    }


def write_theory_canonical(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    payload: dict[str, Any],
    settings: Settings | None,
) -> dict[str, Any]:
    theory = write_theory(conn, TheoryIn(**payload), settings=settings)
    out = get_object(conn, workspace_id=workspace_id, kind="theory", object_id=theory.id) or {
        "id": theory.id,
        "kind": "theory",
    }
    return {
        **out,
        "theory_id": theory.id,
        "validation_criteria": theory.validation_criteria,
        "source_episode_id": theory.source_episode_id,
        "capability_suggestions": capability_suggestion_dicts(
            conn,
            workspace_id=workspace_id,
            title=theory.title,
            text=theory.claim,
            rationale=theory.mechanism or "",
        ),
    }
