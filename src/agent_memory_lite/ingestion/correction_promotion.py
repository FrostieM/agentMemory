"""Promote a correction candidate to a behavior instruction.

Used by the FastAPI route (``api/routes/promote_to_behavior.py``). The
MCP stdio surface is v3-only and no longer exposes the old review tools.

Trust gate stays intact: the candidate must exist, be ``status='new'``,
and have ``kind='correction'``. The function never bypasses
``upsert_behavior_instruction`` — every promotion runs the standard
behavior-writer transaction with audit log. Pre-promote guards live
in ``correction_promotion_guards.py``; the orchestrator below stays
under the 150-SLOC ceiling.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.ingestion.correction_promotion_guards import (
    CorrectionPromotionError,
    guard_name_taken,
    load_pending_correction,
)
from agent_memory_lite.ingestion.pin_service import pin_memory_object
from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionIn
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now

__all__ = [
    "CorrectionPromoteInput",
    "CorrectionPromotionError",
    "promote_correction_to_behavior",
]


@dataclass(frozen=True, slots=True)
class CorrectionPromoteInput:
    workspace_id: str
    candidate_id: str
    name: str
    rule_text_override: str | None = None
    rationale: str = ""
    kind: BehaviorInstructionKind = BehaviorInstructionKind.OPERATING_RULE
    scope: BehaviorInstructionScope = BehaviorInstructionScope.WORKSPACE
    priority: BehaviorInstructionPriority = BehaviorInstructionPriority.USER_PREFERENCE
    conflict_policy: BehaviorConflictPolicy = BehaviorConflictPolicy.CURRENT_USER_WINS
    applies_to: tuple[str, ...] = ()
    decided_by: str | None = None
    pinned: bool = False
    # Without ``overwrite=True`` promote refuses to clobber an active
    # behavior_instruction with the same name; prevents silent rule
    # replacement via the (workspace_id, name) UPSERT semantics.
    overwrite: bool = False


def promote_correction_to_behavior(
    conn: sqlite3.Connection, payload: CorrectionPromoteInput
) -> BehaviorInstruction:
    """Promote a CORRECTION-kind candidate into a behavior_instruction.

    Atomicity (1.2.1): all three writes — behavior_instruction upsert,
    optional pin, candidate flip + audit — execute inside one outer
    ``with_tx``. Inner helpers' own ``with_tx`` calls become SAVEPOINTs
    (see ``db/transactions.py``). If the candidate flip raises (rare:
    SQL error, integrity violation), the entire promotion is rolled
    back as a unit so the caller never observes a "rule live but
    candidate still status='new'" inconsistency. Raises
    ``CorrectionPromotionError`` (codes ``not_found`` / ``wrong_kind``
    / ``already_decided`` / ``name_taken``) when promotion is blocked.
    Historical design notes live in git history.
    """
    row = load_pending_correction(
        conn, workspace_id=payload.workspace_id, candidate_id=payload.candidate_id
    )
    guard_name_taken(
        conn,
        workspace_id=payload.workspace_id,
        name=payload.name,
        overwrite=payload.overwrite,
    )

    rule_text = payload.rule_text_override or str(row["subject"])
    confidence = float(row["confidence"] or 0.5)

    with with_tx(conn):
        # Step 1: write the behavior_instruction (uses inner SAVEPOINT).
        instruction = upsert_behavior_instruction(
            conn,
            BehaviorInstructionIn(
                workspace_id=payload.workspace_id,
                name=payload.name,
                rule=rule_text,
                kind=payload.kind,
                scope=payload.scope,
                priority=payload.priority,
                rationale=payload.rationale,
                applies_to=list(payload.applies_to),
                conflict_policy=payload.conflict_policy,
                source_episode_id=(
                    str(row["source_episode_id"]) if row["source_episode_id"] else None
                ),
                source_type="memory_candidate",
                source_id=payload.candidate_id,
                reviewed_by=payload.decided_by,
                reviewed_at=iso_now(),
                confidence=confidence,
                active=True,
            ),
        )
        # Step 2: optional pin.
        if payload.pinned:
            pin_memory_object(
                conn,
                workspace_id=payload.workspace_id,
                kind="behavior",
                object_id=instruction.id,
                pinned=True,
            )

        # Step 3: candidate flip + audit row.
        now_iso = iso_now()
        conn.execute(
            """
            UPDATE candidates
            SET status = 'promoted',
                promoted_target_type = 'behavior_instruction',
                promoted_target_id = ?,
                decided_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (instruction.id, now_iso, now_iso, payload.candidate_id),
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="memory_candidate.promoted_to_behavior",
            target_type="memory_candidate",
            target_id=payload.candidate_id,
            after={
                "behavior_instruction_id": instruction.id,
                "decided_by": payload.decided_by,
                "kind": payload.kind.value,
                "pinned": payload.pinned,
                # ``overwrite=true`` indicates the operator replaced an
                # existing behavior_instruction with the same name; record
                # this for audit history so a reviewer can distinguish
                # "appended" vs "replaced" promotions.
                "overwrite": payload.overwrite,
            },
        )
    return instruction
