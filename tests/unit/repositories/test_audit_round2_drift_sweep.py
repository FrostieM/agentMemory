"""v3.5 sector-1 audit round-2 regression locks.

The audit found six more raw ``EnumCls(row["col"])`` parsers with the
same drift-crash class that took down ``/memory/get_context`` for ~2.5h
on copyBot back in v3.4. All six now use ``coerce_enum``. These tests
fix the contract so a future revert is caught immediately.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
    DecisionStatus,
    MaintenanceEventStatus,
    MaintenanceSeverity,
    MemoryCandidateKind,
    MemoryCandidateStatus,
    TheoryStatus,
    TrustLevel,
)
from agent_memory_lite.repositories.candidates_repo import _row_to_candidate
from agent_memory_lite.repositories.capability_links_search import _row_to_link
from agent_memory_lite.repositories.decisions_references import row_to_decision
from agent_memory_lite.repositories.facts_repo import _row_to_fact
from agent_memory_lite.repositories.maintenance_queries import row_to_event
from agent_memory_lite.repositories.theories_search import row_to_theory


def _ws_seed(conn: sqlite3.Connection, ws: str = "drift-sweep-ws") -> str:
    """Ensure workspace_meta has a row so cross-checks downstream pass."""
    conn.execute(
        "INSERT OR IGNORE INTO workspace_meta (workspace_id, key, value, updated_at) "
        "VALUES (?, 'placeholder', '1', '2026-05-20T00:00:00+00:00')",
        (ws,),
    )
    return ws


def test_row_to_decision_tolerates_unknown_status(applied_conn: sqlite3.Connection) -> None:
    """A decision row with a status string the enum doesn't list must
    NOT crash row_to_decision — it degrades to ACTIVE."""
    ws = _ws_seed(applied_conn)
    applied_conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence, importance,
            valid_from, valid_to, created_at, updated_at, pinned)
           VALUES ('dec_drift', ?, 't', 'b', NULL, 'future_status_label', NULL, NULL,
                   0.5, 0.5, '2026-05-20T00:00:00+00:00', NULL,
                   '2026-05-20T00:00:00+00:00', '2026-05-20T00:00:00+00:00', 0)""",
        (ws,),
    )
    row = applied_conn.execute("SELECT * FROM decisions WHERE id = 'dec_drift'").fetchone()
    decision = row_to_decision(row)
    assert decision.status is DecisionStatus.ACTIVE


def test_row_to_theory_tolerates_unknown_status(applied_conn: sqlite3.Connection) -> None:
    ws = _ws_seed(applied_conn, "drift-th-ws")
    applied_conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, domain, claim, mechanism,
            predictions_json, validation_criteria_json, experiment_plan,
            tags_json, status, confidence, importance, source_episode_id,
            evidence_count, evidence_strength, created_at, updated_at)
           VALUES ('th_drift', ?, 't', 'd', 'c', '', '[]', '[]', '',
                   '[]', 'rogue_status', 0.5, 0.5, NULL, 0, 0.0,
                   '2026-05-20T00:00:00+00:00', '2026-05-20T00:00:00+00:00')""",
        (ws,),
    )
    row = applied_conn.execute("SELECT * FROM theories WHERE id = 'th_drift'").fetchone()
    theory = row_to_theory(row)
    assert theory.status is TheoryStatus.PROPOSED


def test_row_to_candidate_tolerates_all_three_unknown_enums(
    applied_conn: sqlite3.Connection,
) -> None:
    ws = _ws_seed(applied_conn, "drift-cand-ws")
    # First need a source episode (FK on memory_candidates is NOT NULL).
    applied_conn.execute(
        """INSERT INTO episodes (id, workspace_id, source_type, raw_text, summary,
            label, trust_level, importance, confidence, created_at, metadata_json)
           VALUES ('ep_seed', ?, 'agent_action', 'fixture', NULL, NULL,
                   'agent_observed', 0.5, 0.5,
                   '2026-05-20T00:00:00+00:00', '{}')""",
        (ws,),
    )
    applied_conn.execute(
        """INSERT INTO memory_candidates
           (id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, temporal_json, write_targets_json,
            metadata_json, source_episode_id, status, created_at, updated_at)
           VALUES ('cand_drift', ?, 'future_kind', 's', '', 'o', 'ev',
                   0.5, 0.5, 'future_trust', '{}', '[]', '{}', 'ep_seed',
                   'future_status', '2026-05-20T00:00:00+00:00',
                   '2026-05-20T00:00:00+00:00')""",
        (ws,),
    )
    row = applied_conn.execute("SELECT * FROM memory_candidates WHERE id = 'cand_drift'").fetchone()
    cand = _row_to_candidate(row)
    assert cand.kind is MemoryCandidateKind.PROJECT_FACT
    assert cand.trust_level is TrustLevel.UNKNOWN
    assert cand.status is MemoryCandidateStatus.NEW


def test_row_to_link_tolerates_all_three_unknown_enums(
    applied_conn: sqlite3.Connection,
) -> None:
    ws = _ws_seed(applied_conn, "drift-link-ws")
    applied_conn.execute(
        """INSERT INTO capability_links
           (id, workspace_id, target_type, target_id, capability_type, capability_id,
            capability_name, relation, rationale, strength, source_episode_id,
            created_at, updated_at)
           VALUES ('caplink_drift', ?, 'future_target', 'tid', 'future_cap', 'cid',
                   'cap', 'future_relation', 'r', 0.5, NULL,
                   '2026-05-20T00:00:00+00:00', '2026-05-20T00:00:00+00:00')""",
        (ws,),
    )
    row = applied_conn.execute(
        "SELECT * FROM capability_links WHERE id = 'caplink_drift'"
    ).fetchone()
    link = _row_to_link(row)
    assert link.target_type is CapabilityLinkTargetType.DECISION
    assert link.capability_type is CapabilityType.SKILL
    assert link.relation is CapabilityLinkRelation.METHOD


def test_row_to_event_tolerates_unknown_severity_and_status(
    applied_conn: sqlite3.Connection,
) -> None:
    ws = _ws_seed(applied_conn, "drift-maint-ws")
    applied_conn.execute(
        """INSERT INTO maintenance_events
           (id, workspace_id, kind, severity, status, summary, details_json,
            source_episode_id, target_type, target_id, created_at, resolved_at)
           VALUES ('me_drift', ?, 'k', 'future_sev', 'future_status',
                   'summary', '{}', NULL, NULL, NULL,
                   '2026-05-20T00:00:00+00:00', NULL)""",
        (ws,),
    )
    row = applied_conn.execute("SELECT * FROM maintenance_events WHERE id = 'me_drift'").fetchone()
    event = row_to_event(row)
    assert event.severity is MaintenanceSeverity.WARNING
    assert event.status is MaintenanceEventStatus.OPEN


def test_row_to_fact_tolerates_unknown_trust_level(
    applied_conn: sqlite3.Connection,
) -> None:
    ws = _ws_seed(applied_conn, "drift-fact-ws")
    applied_conn.execute(
        """INSERT INTO entities (id, workspace_id, type, canonical_name,
            aliases_json, properties_json, created_at, updated_at)
           VALUES ('ent_a', ?, 'project', 'a', '[]', '{}',
                   '2026-05-20T00:00:00+00:00', '2026-05-20T00:00:00+00:00')""",
        (ws,),
    )
    applied_conn.execute(
        """INSERT INTO episodes (id, workspace_id, source_type, raw_text, summary,
            label, trust_level, importance, confidence, created_at, metadata_json)
           VALUES ('ep_fact', ?, 'agent_action', 'fix', NULL, NULL,
                   'agent_observed', 0.5, 0.5,
                   '2026-05-20T00:00:00+00:00', '{}')""",
        (ws,),
    )
    applied_conn.execute(
        """INSERT INTO facts
           (id, workspace_id, subject_entity_id, relation, object_entity_id,
            literal_value, fact_text, source_episode_id, confidence, importance,
            trust_level, observed_at, valid_from, valid_to, invalidated_by_fact_id,
            created_at, metadata_json)
           VALUES ('fact_drift', ?, 'ent_a', 'rel', NULL, 'lit', 'fact',
                   'ep_fact', 0.5, 0.5, 'future_trust',
                   '2026-05-20T00:00:00+00:00', '2026-05-20T00:00:00+00:00',
                   NULL, NULL, '2026-05-20T00:00:00+00:00', '{}')""",
        (ws,),
    )
    row = applied_conn.execute("SELECT * FROM facts WHERE id = 'fact_drift'").fetchone()
    fact = _row_to_fact(row)
    assert fact.trust_level is TrustLevel.UNKNOWN


def test_row_to_instruction_tolerates_all_four_unknown_enums(
    applied_conn: sqlite3.Connection,
) -> None:
    """Behavior instructions ride every envelope — drift here would 500
    the brief + get_context. Hardest case because the priority+scope
    enum values are ALSO keys in PRIORITY_WEIGHT / SCOPE_WEIGHT dicts;
    fallback values MUST exist in those dicts to avoid a KeyError chain."""
    from agent_memory_lite.repositories.behavior_repo_ranking import (  # noqa: PLC0415
        row_to_instruction,
    )

    ws = _ws_seed(applied_conn, "drift-beh-ws")
    applied_conn.execute(
        """INSERT INTO behavior_instructions
           (id, workspace_id, name, kind, scope, priority, rule, rationale,
            applies_to_json, conflict_policy, source_episode_id, confidence,
            active, created_at, updated_at, source_type, application_count, pinned)
           VALUES ('beh_drift', ?, 'n', 'future_kind', 'future_scope',
                   'future_priority', 'rule', 'rat', '[]', 'future_policy',
                   NULL, 0.5, 1, '2026-05-20T00:00:00+00:00',
                   '2026-05-20T00:00:00+00:00', 'manual', 0, 0)""",
        (ws,),
    )
    row = applied_conn.execute(
        "SELECT * FROM behavior_instructions WHERE id = 'beh_drift'"
    ).fetchone()
    inst = row_to_instruction(row)
    assert inst.kind is BehaviorInstructionKind.OPERATING_RULE
    assert inst.scope is BehaviorInstructionScope.GLOBAL
    assert inst.priority is BehaviorInstructionPriority.SUGGESTION
    assert inst.conflict_policy is BehaviorConflictPolicy.LATEST_WINS


def test_date_range_clause_rejects_non_allowlisted_column() -> None:
    """SQL-injection defense: only ``_ALLOWED_DATE_COLUMNS`` may be
    interpolated into the f-string. An attempt to pass an arbitrary
    column name must raise ValueError fast and loud."""
    import pytest  # noqa: PLC0415

    from agent_memory_lite.utils.sql_filters import date_range_clause  # noqa: PLC0415

    # Known good
    sql, params = date_range_clause(since="2026-01-01", until=None, column="created_at")
    assert "created_at >= ?" in sql
    assert params == ["2026-01-01"]
    # Reject anything else, including SQL-injection shapes
    with pytest.raises(ValueError, match="not in the allow-list"):
        date_range_clause(since="2026-01-01", until=None, column="evil; DROP TABLE")
    with pytest.raises(ValueError, match="not in the allow-list"):
        date_range_clause(since=None, until="2026-01-01", column="user_supplied_col")


def test_ingest_file_redacts_secrets_before_chunking() -> None:
    """A file whose body contains an API-key-shaped string must NOT
    land in chunks.text verbatim. The file_pipeline now calls
    ``redact`` on the body before chunking."""
    # Use a fresh in-memory DB
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations  # noqa: PLC0415
    from agent_memory_lite.ingestion.file_pipeline import ingest_file  # noqa: PLC0415

    conn = open_connection(":memory:")  # type: ignore[arg-type]
    apply_migrations(conn, MIGRATION_DIR)
    # An OpenAI-style key + a github token-shaped string.
    secret_content = (
        "Hello world.\n"
        "openai_key = sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF\n"
        "github = ghp_abcdefghijklmnopqrstuvwxyz0123456789AB\n"
        "Bottom of file.\n"
    )
    result = ingest_file(
        conn,
        workspace_id="redact-test-ws",
        path="src/secret.py",
        content=secret_content,
    )
    assert result.skipped is False
    # The raw secret strings must NOT appear in any chunk text.
    rows = conn.execute(
        "SELECT text FROM chunks WHERE workspace_id = ?", ("redact-test-ws",)
    ).fetchall()
    all_text = " ".join(r[0] or "" for r in rows)
    assert "sk-proj-abcdefghij" not in all_text
    assert "ghp_abcdefghij" not in all_text
