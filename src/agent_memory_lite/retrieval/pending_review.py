"""Load pending canonical candidates for health and review surfaces."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.enums import MemoryCandidateKind
from agent_memory_lite.retrieval.pending_review_loaders import (
    count_pending_kind,
    load_candidate_items,
)
from agent_memory_lite.retrieval.pending_review_models import (
    PendingReviewItem,
    PendingReviewSummary,
)

__all__ = [
    "PendingReviewItem",
    "PendingReviewSummary",
    "load_pending_review",
]

_PER_KIND_LIMIT = 5


def load_pending_review(conn: sqlite3.Connection, *, workspace_id: str) -> PendingReviewSummary:
    """Build a PendingReviewSummary from the single canonical queue."""
    decision_count = count_pending_kind(
        conn, workspace_id=workspace_id, kind=MemoryCandidateKind.DECISION
    )
    insight_count = count_pending_kind(
        conn, workspace_id=workspace_id, kind=MemoryCandidateKind.INSIGHT
    )
    correction_count = count_pending_kind(
        conn, workspace_id=workspace_id, kind=MemoryCandidateKind.CORRECTION
    )
    items: list[PendingReviewItem] = []
    for kind, count in (
        (MemoryCandidateKind.DECISION, decision_count),
        (MemoryCandidateKind.INSIGHT, insight_count),
        (MemoryCandidateKind.CORRECTION, correction_count),
    ):
        if count > 0:
            items.extend(
                load_candidate_items(
                    conn,
                    workspace_id=workspace_id,
                    kind=kind,
                    limit=_PER_KIND_LIMIT,
                )
            )
    return PendingReviewSummary(
        decision_review_count=decision_count,
        insight_review_count=insight_count,
        correction_review_count=correction_count,
        items=items,
    )
