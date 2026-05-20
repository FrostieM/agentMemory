"""1.3.0: helpers for the decisions.references_json column.

Split from ``decisions_repo.py`` to keep the repo under the 150-SLOC
ceiling. ``references_json`` stores a list of file paths or
``path:symbol`` markers; /memory/explain_diff and /memory/cold_decisions
read this column.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.enums import DecisionStatus, coerce_enum


def parse_references(row: sqlite3.Row) -> list[str]:
    """Read references_json off a row, tolerating legacy schemas where
    the column is missing (returns empty list) and bad JSON (also
    returns empty list — never raises)."""
    if "references_json" not in row.keys():  # noqa: SIM118
        return []
    raw = row["references_json"]
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x) for x in parsed]


def serialize_references(references: list[str] | None) -> str | None:
    """Encode references list for INSERT. None -> NULL column."""
    if not references:
        return None
    return json.dumps(list(references))


def row_to_decision(row: sqlite3.Row) -> Decision:
    """Map a sqlite3.Row from ``decisions`` (or compatible SELECT) to
    a Decision. Tolerates legacy schemas missing the ``pinned``,
    ``references_json``, or ``outcome_score`` columns.
    """
    keys = row.keys()
    pinned_value = row["pinned"] if "pinned" in keys else 0
    # v3.0.0-final: outcome_score lives in migration 0002_outcome_loop.sql.
    # Tolerate pre-migration rows so the wire surface stays compatible
    # for workspaces that haven't run brain_pass yet.
    outcome_value = row["outcome_score"] if "outcome_score" in keys else 0.0
    return Decision(
        id=row["id"],
        workspace_id=row["workspace_id"],
        title=row["title"],
        decision_text=row["decision_text"],
        rationale=row["rationale"],
        # v3.5 audit-followup: tolerate unknown future status strings so
        # the read path degrades to ACTIVE instead of crashing every
        # /memory/get_context (same pattern as evidence-kind / chunk-kind).
        status=coerce_enum(DecisionStatus, row["status"], DecisionStatus.ACTIVE),
        supersedes_decision_id=row["supersedes_decision_id"],
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        pinned=bool(pinned_value),
        references=parse_references(row),
        outcome_score=float(outcome_value or 0.0),
    )


def insert_decision_with_refs(
    conn: sqlite3.Connection,
    *,
    params_without_refs: tuple[object, ...],
    refs_json: str | None,
) -> None:
    """INSERT a decision row with references_json, falling back to the
    pre-0027 schema (without the column) so legacy DBs keep working.

    ``params_without_refs`` is the 13-tuple matching the legacy INSERT;
    when the new column is available we append refs_json as the 14th.
    Split out of decisions_repo.insert_decision_row to keep that module
    under the 150-SLOC ceiling.
    """
    try:
        conn.execute(
            """
            INSERT INTO decisions (
                id, workspace_id, title, decision_text, rationale, status,
                supersedes_decision_id, source_episode_id, confidence, importance,
                valid_from, valid_to, created_at, updated_at, references_json
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (*params_without_refs, refs_json),
        )
    except sqlite3.OperationalError:
        conn.execute(
            """
            INSERT INTO decisions (
                id, workspace_id, title, decision_text, rationale, status,
                supersedes_decision_id, source_episode_id, confidence, importance,
                valid_from, valid_to, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            params_without_refs,
        )
