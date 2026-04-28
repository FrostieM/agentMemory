"""SQL operations for reviewable memory candidates."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from agent_memory_lite.models.candidates import MemoryCandidate, StoredMemoryCandidate
from agent_memory_lite.models.enums import MemoryCandidateKind, MemoryCandidateStatus, TrustLevel

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


def _json_dict(raw: str | None) -> dict[str, Any]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def _json_list(raw: str | None) -> list[str]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _row_to_candidate(row: sqlite3.Row) -> StoredMemoryCandidate:
    return StoredMemoryCandidate(
        id=row["id"],
        workspace_id=row["workspace_id"],
        kind=MemoryCandidateKind(row["kind"]),
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        evidence=row["evidence"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        trust_level=TrustLevel(row["trust_level"]),
        temporal=_json_dict(row["temporal_json"]),
        write_targets=_json_list(row["write_targets_json"]),
        metadata=_json_dict(row["metadata_json"]),
        source_episode_id=row["source_episode_id"],
        status=MemoryCandidateStatus(row["status"]),
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
        INSERT INTO memory_candidates (
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
    row = conn.execute("SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)).fetchone()
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
        UPDATE memory_candidates
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


def _tokens(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def _searchable_text(candidate: StoredMemoryCandidate) -> str:
    return " ".join(
        [
            candidate.kind.value,
            candidate.subject,
            candidate.predicate,
            candidate.object or "",
            candidate.evidence,
        ]
    ).lower()


def _rank(candidate: StoredMemoryCandidate, tokens: list[str]) -> tuple[float, str]:
    text = _searchable_text(candidate)
    token_score = sum(1.0 for token in tokens if token in text)
    status_bonus = 0.2 if candidate.status == MemoryCandidateStatus.NEW else 0.0
    score = token_score + candidate.importance + (candidate.confidence * 0.25) + status_bonus
    return score, candidate.updated_at


def list_candidates(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    statuses: list[MemoryCandidateStatus] | None = None,
    limit: int = 20,
) -> list[StoredMemoryCandidate]:
    rows = conn.execute(
        "SELECT * FROM memory_candidates WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    candidates = [_row_to_candidate(row) for row in rows]
    if statuses is not None:
        allowed = set(statuses)
        candidates = [candidate for candidate in candidates if candidate.status in allowed]
    terms = _tokens(query)
    if terms:
        candidates = [
            candidate
            for candidate in candidates
            if any(token in _searchable_text(candidate) for token in terms)
        ]
    candidates.sort(key=lambda candidate: _rank(candidate, terms), reverse=True)
    return candidates[:limit]
