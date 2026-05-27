"""Canonical ``candidates`` loaders for the ``<pending_review>`` surface."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from agent_memory_lite.models.enums import MemoryCandidateKind, MemoryCandidateStatus
from agent_memory_lite.retrieval.pending_review_models import PendingReviewItem


def count_pending_kind(
    conn: sqlite3.Connection, *, workspace_id: str, kind: MemoryCandidateKind
) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM candidates
            WHERE workspace_id = ? AND status = ? AND kind = ?
            """,
            (workspace_id, MemoryCandidateStatus.NEW.value, kind.value),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def load_candidate_items(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: MemoryCandidateKind,
    limit: int,
) -> Iterable[PendingReviewItem]:
    try:
        rows = conn.execute(
            """
            SELECT id, kind, subject, confidence, metadata_json
            FROM candidates
            WHERE workspace_id = ? AND status = ? AND kind = ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (workspace_id, MemoryCandidateStatus.NEW.value, kind.value, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        PendingReviewItem(
            kind=str(row["kind"]),
            id=str(row["id"]),
            title=str(row["subject"])[:120],
            extra=_candidate_extra(kind=kind, row=row),
        )
        for row in rows
    ]


def _candidate_extra(*, kind: MemoryCandidateKind, row: sqlite3.Row) -> str:
    metadata = _metadata(row)
    if kind == MemoryCandidateKind.DECISION:
        theory_id = metadata.get("theory_id")
        return f"theory_id={theory_id}" if theory_id else "target=decision"
    if kind == MemoryCandidateKind.INSIGHT:
        insight_type = metadata.get("insight_type")
        return f"insight_type={insight_type}" if insight_type else "target=insight"
    confidence = float(row["confidence"] or 0.0)
    if kind == MemoryCandidateKind.CORRECTION:
        return f"confidence={confidence:.2f} target=behavior"
    return f"confidence={confidence:.2f}"


def _metadata(row: sqlite3.Row) -> dict[str, object]:
    try:
        decoded = json.loads(str(row["metadata_json"] or "{}"))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}
