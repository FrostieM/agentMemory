from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)
from agent_memory_lite.repositories.behavior_repo import list_behavior_instructions


def test_upsert_behavior_instruction_redacts_secret_in_name(
    applied_conn: sqlite3.Connection,
) -> None:
    """Certification finding: `name` is FTS-indexed for behavior; once Batch B syncs
    the writer inline, a secret in the name surfaces into durable_fts. Redact it (the
    blessed write_canonical path already does). The secret must be absent from both
    the stored name and the FTS index."""
    secret_raw = "sk-ant-secret-LEAK-BEHNAME"
    bi = upsert_behavior_instruction(
        applied_conn,
        BehaviorInstructionIn(
            workspace_id="default",
            name=f"rule api_key: {secret_raw}",
            kind=BehaviorInstructionKind.OPERATING_RULE,
            scope=BehaviorInstructionScope.WORKSPACE,
            priority=BehaviorInstructionPriority.USER_PREFERENCE,
            rule="a clean rule body",
            conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
            confidence=0.9,
        ),
    )
    assert secret_raw not in bi.name
    row = applied_conn.execute(
        "SELECT content FROM durable_fts WHERE object_id=?", (bi.id,)
    ).fetchone()
    assert row is not None
    assert secret_raw not in (row[0] or "")


def test_upsert_behavior_instruction_is_fts_searchable(
    applied_conn: sqlite3.Connection,
) -> None:
    """M1 (write-atomicity batch): behavior is a DURABLE_FTS_KIND and
    upsert_behavior_instruction is the single business writer EVERY behavior-
    create path funnels through (write_canonical, correction/candidate promotion,
    promote_durable, project seeding). Syncing FTS inside the writer makes ALL
    those callers searchable at one choke point. This locks the invariant: a row
    produced by the writer is immediately findable by memory_search. An UPSERT that
    reuses the row id must keep the (single) row findable by the new rule text."""
    from agent_memory_lite.storage.reader import search_kind_fts  # noqa: PLC0415

    first = upsert_behavior_instruction(
        applied_conn,
        BehaviorInstructionIn(
            workspace_id="default",
            name="Pangolin review rule",
            kind=BehaviorInstructionKind.OPERATING_RULE,
            scope=BehaviorInstructionScope.WORKSPACE,
            priority=BehaviorInstructionPriority.USER_PREFERENCE,
            rule="Always run the pangolin lint before merging.",
            conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
            confidence=0.9,
        ),
    )
    hits = search_kind_fts(
        applied_conn, workspace_id="default", kind="behavior", query="pangolin lint", limit=5
    )
    assert first.id in [h.projection["id"] for h in hits]

    # UPSERT on the same name reuses first.id and rewrites the indexed rule text;
    # the re-sync must keep the single row findable by the new text.
    updated = upsert_behavior_instruction(
        applied_conn,
        BehaviorInstructionIn(
            workspace_id="default",
            name="Pangolin review rule",
            kind=BehaviorInstructionKind.OPERATING_RULE,
            scope=BehaviorInstructionScope.WORKSPACE,
            priority=BehaviorInstructionPriority.USER_PREFERENCE,
            rule="Always run the armadillo type-check before merging.",
            conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
            confidence=0.95,
        ),
    )
    assert updated.id == first.id
    hits2 = search_kind_fts(
        applied_conn, workspace_id="default", kind="behavior", query="armadillo type-check", limit=5
    )
    assert updated.id in [h.projection["id"] for h in hits2]


def test_behavior_instruction_upserts_reuse_name_per_workspace(
    applied_conn: sqlite3.Connection,
) -> None:
    first = upsert_behavior_instruction(
        applied_conn,
        BehaviorInstructionIn(
            workspace_id="default",
            name="Direct technical tone",
            kind=BehaviorInstructionKind.COMMUNICATION_STYLE,
            scope=BehaviorInstructionScope.WORKSPACE,
            priority=BehaviorInstructionPriority.USER_PREFERENCE,
            rule="Answer directly, cite evidence, and avoid vague reassurance.",
            conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
            confidence=0.9,
        ),
    )
    updated = upsert_behavior_instruction(
        applied_conn,
        BehaviorInstructionIn(
            workspace_id="default",
            name="Direct technical tone",
            kind=BehaviorInstructionKind.COMMUNICATION_STYLE,
            scope=BehaviorInstructionScope.WORKSPACE,
            priority=BehaviorInstructionPriority.USER_PREFERENCE,
            rule="Answer directly, cite exact evidence, and avoid vague reassurance.",
            applies_to=["status reports", "debugging"],
            conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
            confidence=0.95,
        ),
    )

    assert updated.id == first.id
    assert updated.confidence == 0.95
    assert updated.applies_to == ["status reports", "debugging"]
    assert "exact evidence" in updated.rule


def test_behavior_instruction_governance_fields_round_trip(
    applied_conn: sqlite3.Connection,
) -> None:
    stored = upsert_behavior_instruction(
        applied_conn,
        BehaviorInstructionIn(
            workspace_id="default",
            name="Reviewed operating contract",
            kind=BehaviorInstructionKind.OPERATING_RULE,
            scope=BehaviorInstructionScope.PROJECT,
            priority=BehaviorInstructionPriority.PROJECT_CONVENTION,
            rule="Use the reviewed project operating contract for memory writes.",
            conflict_policy=BehaviorConflictPolicy.HIGHER_PRIORITY_WINS,
            source_type="candidate_review",
            source_id="cand_reviewed_contract",
            reviewed_by="test-reviewer",
            reviewed_at="2026-04-30T00:00:00+00:00",
            expires_at="2999-01-01T00:00:00+00:00",
            conflict_group="memory-write-contract",
            confidence=0.91,
        ),
    )

    assert stored.source_type == "candidate_review"
    assert stored.source_id == "cand_reviewed_contract"
    assert stored.reviewed_by == "test-reviewer"
    assert stored.reviewed_at == "2026-04-30T00:00:00+00:00"
    assert stored.expires_at == "2999-01-01T00:00:00+00:00"
    assert stored.conflict_group == "memory-write-contract"
    assert stored.application_count == 0


def test_behavior_instructions_rank_by_query_priority_and_scope(
    applied_conn: sqlite3.Connection,
) -> None:
    upsert_behavior_instruction(
        applied_conn,
        BehaviorInstructionIn(
            workspace_id="default",
            name="Generic suggestion",
            kind=BehaviorInstructionKind.WORKFLOW_PREFERENCE,
            scope=BehaviorInstructionScope.GLOBAL,
            priority=BehaviorInstructionPriority.SUGGESTION,
            rule="Use a compact answer when the task is trivial.",
            confidence=0.7,
        ),
    )
    upsert_behavior_instruction(
        applied_conn,
        BehaviorInstructionIn(
            workspace_id="default",
            name="Evidence-first operations",
            kind=BehaviorInstructionKind.OPERATING_RULE,
            scope=BehaviorInstructionScope.PROJECT,
            priority=BehaviorInstructionPriority.PROJECT_CONVENTION,
            rule="For runtime operations, report exact endpoint state and command evidence.",
            confidence=0.9,
        ),
    )

    items = list_behavior_instructions(
        applied_conn,
        workspace_id="default",
        query="runtime endpoint evidence",
        limit=5,
    )

    assert [item.name for item in items] == ["Evidence-first operations"]


def test_expired_behavior_instruction_is_not_applicable_by_default(
    applied_conn: sqlite3.Connection,
) -> None:
    upsert_behavior_instruction(
        applied_conn,
        BehaviorInstructionIn(
            workspace_id="default",
            name="Expired workflow preference",
            kind=BehaviorInstructionKind.WORKFLOW_PREFERENCE,
            scope=BehaviorInstructionScope.WORKSPACE,
            priority=BehaviorInstructionPriority.USER_PREFERENCE,
            rule="This old preference should not be applied.",
            expires_at="2000-01-01T00:00:00+00:00",
            confidence=0.9,
        ),
    )

    applicable = list_behavior_instructions(
        applied_conn,
        workspace_id="default",
        query="old preference",
        limit=5,
    )
    all_items = list_behavior_instructions(
        applied_conn,
        workspace_id="default",
        query="old preference",
        include_inactive=True,
        limit=5,
    )

    assert applicable == []
    assert [item.name for item in all_items] == ["Expired workflow preference"]
