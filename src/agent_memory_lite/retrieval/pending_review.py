"""Surface pending decision_candidates / insight_candidates / correction_candidates
in envelopes (v1.7 + v1.8 + v1.10).

Originally v1.7 left pending candidates only on the dedicated review
endpoints, so the agent had to remember to poll. The envelope-surface
improvement adds a ``<pending_review>`` block to ``/memory/get_context``
so the agent (and operator inspecting the rendered context) sees the
queue every time it reads memory. v1.10 extends the same surface with
``correction_candidate`` rows from ``memory_candidates`` so corrections
captured by the hook show up alongside decision and insight queues.

Read-only: this module never writes. The renderer is pure XML; the
loader does small SQL count + LIMIT 5 fetches per kind — cheap on every
call. Per-kind SQL lives in ``pending_review_loaders``.
"""

from __future__ import annotations

import sqlite3
from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.retrieval.pending_review_loaders import (
    count_pending,
    count_pending_corrections,
    load_correction_items,
    load_decision_items,
    load_insight_items,
)
from agent_memory_lite.retrieval.pending_review_models import (
    PendingReviewItem,
    PendingReviewSummary,
)

__all__ = [
    "PendingReviewItem",
    "PendingReviewSummary",
    "load_pending_review",
    "render_pending_review",
]

_PER_KIND_LIMIT = 5


def load_pending_review(conn: sqlite3.Connection, *, workspace_id: str) -> PendingReviewSummary:
    """Build a PendingReviewSummary aggregating all candidate kinds.

    Hub mode may route requests to a DB that pre-dates migration 0023
    (decision_candidates) or 0024 (insight_candidates). Each loader
    handles the missing-table case as "queue empty" rather than
    raising. v1.10's correction queue lives in memory_candidates,
    which is part of the base schema and always present.
    """
    decision_count = count_pending(conn, "decision_candidates", workspace_id)
    insight_count = count_pending(conn, "insight_candidates", workspace_id)
    correction_count = count_pending_corrections(conn, workspace_id)
    items: list[PendingReviewItem] = []
    if decision_count > 0:
        items.extend(load_decision_items(conn, workspace_id, _PER_KIND_LIMIT))
    if insight_count > 0:
        items.extend(load_insight_items(conn, workspace_id, _PER_KIND_LIMIT))
    if correction_count > 0:
        items.extend(load_correction_items(conn, workspace_id, _PER_KIND_LIMIT))
    return PendingReviewSummary(
        decision_candidates_count=decision_count,
        insight_candidates_count=insight_count,
        correction_candidates_count=correction_count,
        items=items,
    )


def render_pending_review(summary: PendingReviewSummary) -> list[str]:
    """Render the ``<pending_review>`` XML block. Empty when no pending items."""
    if summary.is_empty():
        return []
    lines = [
        f"  <pending_review "
        f"decision_candidates={quoteattr(str(summary.decision_candidates_count))} "
        f"insight_candidates={quoteattr(str(summary.insight_candidates_count))} "
        f"correction_candidates={quoteattr(str(summary.correction_candidates_count))}>"
    ]
    for item in summary.items:
        attrs = f"id={quoteattr(item.id)} kind={quoteattr(item.kind)} extra={quoteattr(item.extra)}"
        lines.append(f"    <ref {attrs}>{escape(item.title)}</ref>")
    lines.append("  </pending_review>")
    return lines
