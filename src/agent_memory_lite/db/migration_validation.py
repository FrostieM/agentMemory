"""Validation helpers for the squashed v3 migration baseline."""

from __future__ import annotations

import sqlite3

CANONICAL_BASELINE_VERSION = "0001_init"

_CANONICAL_SENTINEL_TABLES = frozenset(
    {
        "behaviors",
        "concepts",
        "insights",
        "plan_steps",
        "retrieval_coactivation",
        "self_model",
        "skills",
        "tasks",
    }
)
_CANONICAL_REQUIRED_COLUMNS = {
    "behaviors": frozenset(
        {"id", "workspace_id", "name", "kind", "rule", "active", "pinned", "valid_from"}
    ),
    "concepts": frozenset({"id", "workspace_id", "name", "kind", "definition", "active"}),
    "insights": frozenset({"id", "workspace_id", "insight_type", "summary", "status"}),
    "plan_steps": frozenset({"id", "workspace_id", "task_id", "rank", "title", "status"}),
    "retrieval_coactivation": frozenset(
        {"id", "workspace_id", "query_hash", "item_kind", "item_id", "rank"}
    ),
    "self_model": frozenset(
        {"workspace_id", "identity_text", "invariants_json", "uncertainties_json"}
    ),
    "skills": frozenset({"id", "workspace_id", "name", "subtype", "summary", "body_md", "active"}),
    "tasks": frozenset({"id", "workspace_id", "task_id", "goal", "status", "current_plan_json"}),
}
_LEGACY_V2_TABLES = frozenset(
    {
        "active_edits",
        "agent_playbooks",
        "agent_roles",
        "agent_skills",
        "behavior_instructions",
        "core_memory",
        "domain_concepts",
        "decision_candidates",
        "file_digests",
        "insight_candidates",
        "memory_candidates",
        "memory_snapshots",
        "memory_state_snapshots",
        "procedural_rules",
        "research_experiments",
        "research_insights",
        "task_state",
    }
)


def validate_canonical_baseline(conn: sqlite3.Connection) -> None:
    tables = _table_names(conn)
    missing = sorted(_CANONICAL_SENTINEL_TABLES - tables)
    legacy = sorted(_LEGACY_V2_TABLES & tables)
    malformed = _malformed_canonical_tables(conn, missing)
    if not missing and not legacy and not malformed:
        return
    raise RuntimeError(
        "schema_migrations marks 0001_init applied, but this database is not "
        "the squashed canonical v3 baseline ("
        + "; ".join(_baseline_error_details(missing, legacy, malformed))
        + "). Back up/export the old data and rebootstrap a fresh v3 database."
    )


def reject_legacy_pre_v3_db(conn: sqlite3.Connection) -> None:
    legacy = sorted(_LEGACY_V2_TABLES & _table_names(conn))
    if not legacy:
        return
    raise RuntimeError(
        "Refusing to apply the squashed v3 0001_init migration on top of a "
        "pre-v3 database with legacy tables: "
        + ", ".join(legacy)
        + ". Back up and rebootstrap into a fresh v3 database; do not create "
        "a hybrid active schema."
    )


def _baseline_error_details(
    missing: list[str],
    legacy: list[str],
    malformed: list[str],
) -> list[str]:
    details: list[str] = []
    if missing:
        details.append(f"missing canonical v3 tables: {', '.join(missing)}")
    if legacy:
        details.append(f"legacy v2 tables still present: {', '.join(legacy)}")
    if malformed:
        details.append(f"malformed canonical v3 tables: {'; '.join(malformed)}")
    return details


def _malformed_canonical_tables(conn: sqlite3.Connection, missing: list[str]) -> list[str]:
    if missing:
        return []
    malformed: list[str] = []
    for table, required_columns in sorted(_CANONICAL_REQUIRED_COLUMNS.items()):
        missing_columns = sorted(required_columns - _table_columns(conn, table))
        if missing_columns:
            malformed.append(f"{table} missing columns: {', '.join(missing_columns)}")
    return malformed


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    return {str(row[0]) for row in rows}
