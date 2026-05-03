"""Surface pending decision_candidates / insight_candidates in envelopes (v1.7).

Originally v1.7 left pending candidates only on the dedicated review
endpoints, so the agent had to remember to poll. The envelope-surface
improvement adds a ``<pending_review>`` block to ``/memory/get_context``
so the agent (and operator inspecting the rendered context) sees the
queue every time it reads memory.

Read-only: this module never writes. The renderer is pure XML; the
loader does a small SQL count + LIMIT 5 fetch — cheap on every call.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

_PER_KIND_LIMIT = 5


@dataclass(frozen=True, slots=True)
class PendingReviewItem:
    kind: str  # "decision_candidate" or "insight_candidate"
    id: str
    title: str
    extra: str  # short hint (theory_id / insight_type)


@dataclass(frozen=True, slots=True)
class PendingReviewSummary:
    decision_candidates_count: int
    insight_candidates_count: int
    items: list[PendingReviewItem]

    @property
    def total(self) -> int:
        return self.decision_candidates_count + self.insight_candidates_count

    def is_empty(self) -> bool:
        return self.total == 0


def _count_pending(conn: sqlite3.Connection, table: str, workspace_id: str) -> int:
    """Count pending rows in a candidate table; return 0 if the table is missing.

    Hub mode may route to a DB that pre-dates the table's migration. Treat the
    legacy schema as "queue empty" instead of 500-ing the route.
    """
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ? AND status = 'pending'",
            (workspace_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def load_pending_review(conn: sqlite3.Connection, *, workspace_id: str) -> PendingReviewSummary:
    # Hub mode may route requests to a DB that pre-dates migration 0023
    # (decision_candidates) or 0024 (insight_candidates) — for example a
    # legacy global workspace DB shared with v1.0.x clients. Handle the
    # missing-table case as "queue empty" rather than 500-ing the route.
    decision_count = _count_pending(conn, "decision_candidates", workspace_id)
    insight_count = _count_pending(conn, "insight_candidates", workspace_id)
    items: list[PendingReviewItem] = []
    if decision_count > 0:
        try:
            decision_rows = conn.execute(
                """
                SELECT id, proposed_title, theory_id FROM decision_candidates
                WHERE workspace_id = ? AND status = 'pending'
                ORDER BY updated_at DESC LIMIT ?
                """,
                (workspace_id, _PER_KIND_LIMIT),
            ).fetchall()
        except sqlite3.OperationalError:
            decision_rows = []
        for row in decision_rows:
            items.append(
                PendingReviewItem(
                    kind="decision_candidate",
                    id=str(row["id"]),
                    title=str(row["proposed_title"]),
                    extra=f"from {row['theory_id']}",
                )
            )
    if insight_count > 0:
        try:
            insight_rows = conn.execute(
                """
                SELECT id, summary, insight_type FROM insight_candidates
                WHERE workspace_id = ? AND status = 'pending'
                ORDER BY updated_at DESC LIMIT ?
                """,
                (workspace_id, _PER_KIND_LIMIT),
            ).fetchall()
        except sqlite3.OperationalError:
            insight_rows = []
        for row in insight_rows:
            items.append(
                PendingReviewItem(
                    kind="insight_candidate",
                    id=str(row["id"]),
                    title=str(row["summary"])[:120],
                    extra=str(row["insight_type"]),
                )
            )
    return PendingReviewSummary(
        decision_candidates_count=decision_count,
        insight_candidates_count=insight_count,
        items=items,
    )


def render_pending_review(summary: PendingReviewSummary) -> list[str]:
    """Render the ``<pending_review>`` XML block. Empty when no pending items."""
    if summary.is_empty():
        return []
    lines = [
        f"  <pending_review "
        f"decision_candidates={quoteattr(str(summary.decision_candidates_count))} "
        f"insight_candidates={quoteattr(str(summary.insight_candidates_count))}>"
    ]
    for item in summary.items:
        attrs = f"id={quoteattr(item.id)} kind={quoteattr(item.kind)} extra={quoteattr(item.extra)}"
        lines.append(f"    <ref {attrs}>{escape(item.title)}</ref>")
    lines.append("  </pending_review>")
    return lines
