"""Find ranking-eligible rows that have not been retrieved in too long.

Surfaces rows as ``cold_candidate`` maintenance events. Never archives
directly; archival stays human-driven via the review queue. Pinned rows
(decisions/theories with pinned=1) are excluded — operator-anchored items
are durable by design and shouldn't be flagged just for sitting unused.

Per plan blind spot #4: this only flags candidates. The retrieval layer
remains the source of truth for "should this row exist"; the scanner just
asks the operator to look.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now

# Tables we scan + the ids/timestamp/pinned shape per kind. Only decisions
# carry the pinned column (per v1.0.1 pin endpoint scope: decision /
# behavior_instruction / core_memory). Theories, chunks, and concepts have
# no pinned column, so the per-kind config marks them as has_pinned=False.
_SCAN_CONFIG: tuple[tuple[str, str, str, bool], ...] = (
    # (kind, table, id_column, has_pinned)
    ("chunk", "chunks", "id", False),
    ("decision", "decisions", "id", True),
    ("theory", "theories", "id", False),
    ("concept", "domain_concepts", "id", False),
)


@dataclass(frozen=True, slots=True)
class ColdCandidate:
    kind: str
    id: str
    last_retrieved_at: str

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "id": self.id, "last_retrieved_at": self.last_retrieved_at}


def _select_cold_for(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    table: str,
    id_column: str,
    has_pinned: bool,
    cutoff_iso: str,
) -> list[ColdCandidate]:
    pinned_clause = "AND COALESCE(pinned, 0) = 0" if has_pinned else ""
    query = f"""
        SELECT {id_column} AS row_id, last_retrieved_at
        FROM {table}
        WHERE workspace_id = ?
          AND last_retrieved_at IS NOT NULL
          AND last_retrieved_at < ?
          {pinned_clause}
        ORDER BY last_retrieved_at
    """
    rows = conn.execute(query, (workspace_id, cutoff_iso)).fetchall()
    return [
        ColdCandidate(
            kind=kind, id=str(row["row_id"]), last_retrieved_at=str(row["last_retrieved_at"])
        )
        for row in rows
    ]


def find_cold_candidates(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    older_than_days: int,
) -> list[ColdCandidate]:
    """Read-only scan across all tracked kinds. Returns rows whose
    ``last_retrieved_at`` is older than ``older_than_days``."""
    cutoff_iso = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
    candidates: list[ColdCandidate] = []
    for kind, table, id_column, has_pinned in _SCAN_CONFIG:
        candidates.extend(
            _select_cold_for(
                conn,
                workspace_id=workspace_id,
                kind=kind,
                table=table,
                id_column=id_column,
                has_pinned=has_pinned,
                cutoff_iso=cutoff_iso,
            )
        )
    return candidates


def emit_cold_candidate_events(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    candidates: list[ColdCandidate],
) -> int:
    """Write a single ``cold_candidate`` audit row referencing the batch.

    The audit row is the cheap, durable signal — actual ``maintenance_events``
    rows are written one-per-candidate via the existing maintenance repo
    only when the operator opts into auto-queueing (separate flag). We keep
    the two paths distinct so a workspace can run the scan in observability
    mode without flooding maintenance_events.
    """
    if not candidates:
        return 0
    insert_audit(
        conn,
        workspace_id=workspace_id,
        action="maintenance.cold_candidate_emitted",
        target_type="workspace",
        target_id=workspace_id,
        after={
            "count": len(candidates),
            "by_kind": _count_by_kind(candidates),
            "at": iso_now(),
        },
    )
    return len(candidates)


def _count_by_kind(candidates: list[ColdCandidate]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.kind] = counts.get(candidate.kind, 0) + 1
    return counts
