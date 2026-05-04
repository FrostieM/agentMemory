"""Per-kind loaders for ``<pending_review>`` candidates.

Split out of ``pending_review.py`` so that module stays under the
150-SLOC ceiling. Each helper hides the SQL + missing-table guard for
one candidate kind and returns either a count or a list of
``PendingReviewItem`` rows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from agent_memory_lite.retrieval.pending_review_models import PendingReviewItem


def count_pending(conn: sqlite3.Connection, table: str, workspace_id: str) -> int:
    """Count pending rows in a candidate table; 0 when the table is missing.

    Hub mode may route to a DB that pre-dates the table's migration. Treat
    the legacy schema as "queue empty" instead of 500-ing the route.
    """
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ? AND status = 'pending'",
            (workspace_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def count_pending_corrections(conn: sqlite3.Connection, workspace_id: str) -> int:
    """v1.10: count pending memory_candidate(kind=CORRECTION) rows."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM memory_candidates "
            "WHERE workspace_id = ? AND status = 'new' AND kind = 'correction'",
            (workspace_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def load_decision_items(
    conn: sqlite3.Connection, workspace_id: str, limit: int
) -> Iterable[PendingReviewItem]:
    try:
        rows = conn.execute(
            """
            SELECT id, proposed_title, theory_id FROM decision_candidates
            WHERE workspace_id = ? AND status = 'pending'
            ORDER BY updated_at DESC LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        PendingReviewItem(
            kind="decision_candidate",
            id=str(row["id"]),
            title=str(row["proposed_title"]),
            extra=f"from {row['theory_id']}",
        )
        for row in rows
    ]


def load_insight_items(
    conn: sqlite3.Connection, workspace_id: str, limit: int
) -> Iterable[PendingReviewItem]:
    try:
        rows = conn.execute(
            """
            SELECT id, summary, insight_type FROM insight_candidates
            WHERE workspace_id = ? AND status = 'pending'
            ORDER BY updated_at DESC LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        PendingReviewItem(
            kind="insight_candidate",
            id=str(row["id"]),
            title=str(row["summary"])[:120],
            extra=str(row["insight_type"]),
        )
        for row in rows
    ]


def load_correction_items(
    conn: sqlite3.Connection, workspace_id: str, limit: int
) -> Iterable[PendingReviewItem]:
    try:
        rows = conn.execute(
            """
            SELECT id, subject, confidence FROM memory_candidates
            WHERE workspace_id = ? AND status = 'new' AND kind = 'correction'
            ORDER BY updated_at DESC LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        PendingReviewItem(
            kind="correction_candidate",
            id=str(row["id"]),
            title=str(row["subject"])[:120],
            extra=(
                f"confidence={float(row['confidence']):.2f} "
                "promote via /memory/promote_candidate_to_behavior"
            ),
        )
        for row in rows
    ]
