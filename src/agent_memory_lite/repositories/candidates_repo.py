"""SQL operations for reviewable memory candidates."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.models.candidates import MemoryCandidate, StoredMemoryCandidate
from agent_memory_lite.models.enums import (
    MemoryCandidateKind,
    MemoryCandidateStatus,
    TrustLevel,
    coerce_enum,
)
from agent_memory_lite.repositories.candidates_search import (
    rank_candidate,
    searchable_text,
    tokenize_query,
)
from agent_memory_lite.utils.sql_filters import date_range_clause


def _json_dict(raw: str | None) -> dict[str, Any]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def _json_list(raw: str | None) -> list[str]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _row_to_candidate(row: sqlite3.Row) -> StoredMemoryCandidate:
    # v3.5 audit-followup: three drift sites in one parser. Vector 1/5
    # both wrote new kind literals before the enum caught up; if anyone
    # invents another value, compact reads + /pending_review would
    # 500 every call. Fallback PROJECT_FACT (generic-bucket) for kind,
    # UNKNOWN for trust, NEW for status.
    return StoredMemoryCandidate(
        id=row["id"],
        workspace_id=row["workspace_id"],
        kind=coerce_enum(MemoryCandidateKind, row["kind"], MemoryCandidateKind.PROJECT_FACT),
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        evidence=row["evidence"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        trust_level=coerce_enum(TrustLevel, row["trust_level"], TrustLevel.UNKNOWN),
        temporal=_json_dict(row["temporal_json"]),
        write_targets=_json_list(row["write_targets_json"]),
        metadata=_json_dict(row["metadata_json"]),
        source_episode_id=row["source_episode_id"],
        status=coerce_enum(MemoryCandidateStatus, row["status"], MemoryCandidateStatus.NEW),
        promoted_target_type=row["promoted_target_type"],
        promoted_target_id=row["promoted_target_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        decided_at=row["decided_at"],
    )


def insert_candidate_row(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    workspace_id: str,
    candidate: MemoryCandidate,
    status: MemoryCandidateStatus,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO candidates (
            id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, temporal_json, write_targets_json,
            metadata_json, source_episode_id, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id,
            workspace_id,
            candidate.kind.value,
            candidate.subject,
            candidate.predicate,
            candidate.object,
            candidate.evidence,
            candidate.confidence,
            candidate.importance,
            candidate.trust_level.value,
            candidate.temporal.model_dump_json(),
            json.dumps(candidate.write_targets, sort_keys=True),
            json.dumps(candidate.metadata, sort_keys=True, default=str),
            candidate.source_episode_id,
            status.value,
            created_at,
            created_at,
        ),
    )


def get_candidate(conn: sqlite3.Connection, candidate_id: str) -> StoredMemoryCandidate | None:
    row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    return _row_to_candidate(row) if row is not None else None


def update_candidate_status(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    status: MemoryCandidateStatus,
    updated_at: str,
    promoted_target_type: str | None = None,
    promoted_target_id: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE candidates
        SET status = ?,
            promoted_target_type = COALESCE(?, promoted_target_type),
            promoted_target_id = COALESCE(?, promoted_target_id),
            updated_at = ?,
            decided_at = ?
        WHERE id = ?
        """,
        (
            status.value,
            promoted_target_type,
            promoted_target_id,
            updated_at,
            updated_at,
            candidate_id,
        ),
    )


def list_candidates(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    statuses: list[MemoryCandidateStatus] | None = None,
    limit: int = 20,
    since: str | None = None,
    until: str | None = None,
) -> list[StoredMemoryCandidate]:
    extra_sql, extra_params = date_range_clause(since=since, until=until)
    rows = conn.execute(
        f"SELECT * FROM candidates WHERE workspace_id = ? {extra_sql}",
        (workspace_id, *extra_params),
    ).fetchall()
    candidates = [_row_to_candidate(row) for row in rows]
    if statuses is not None:
        allowed = set(statuses)
        candidates = [candidate for candidate in candidates if candidate.status in allowed]
    terms = tokenize_query(query)
    if terms:
        candidates = [
            candidate
            for candidate in candidates
            if any(token in searchable_text(candidate) for token in terms)
        ]
    candidates.sort(key=lambda candidate: rank_candidate(candidate, terms), reverse=True)
    return candidates[:limit]
