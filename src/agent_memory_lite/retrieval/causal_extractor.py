"""Phase 7: extract causal links from corrected episodes + consolidation insights.

A causal link is the "WHY this row matters" complement to Phase 2's
co-retrieval signal. The extractor scans:

* **Corrected episodes** (``trust_level='corrected'``) that reference
  both a prior decision and a new decision -> emit
  ``invalidated(new_decision, prior_decision)``.
* **Consolidation insights** that cite multiple episodes -> emit
  ``derived_from(insight, episode)`` per citation.

Idempotent: the UNIQUE constraint on
``(workspace_id, src_kind, src_id, dst_kind, dst_id, relation)``
prevents duplicate rows. Re-running on the same corpus is a no-op
beyond a couple of SELECTs.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class CausalReport:
    """Per-workspace extraction summary."""

    workspaces_scanned: int
    invalidated_links: int
    derived_links: int


def _upsert_causal_link(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    src_kind: str,
    src_id: str,
    dst_kind: str,
    dst_id: str,
    relation: str,
    evidence_episode_id: str | None = None,
    weight: float = 1.0,
) -> bool:
    """INSERT OR IGNORE one causal_link. Returns True if newly inserted."""
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO causal_links
               (id, workspace_id, src_kind, src_id, dst_kind, dst_id,
                relation, weight, evidence_episode_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(IdKind.AUDIT),
                workspace_id,
                src_kind,
                src_id,
                dst_kind,
                dst_id,
                relation,
                weight,
                evidence_episode_id,
                iso_now(),
            ),
        )
    except sqlite3.OperationalError:
        return False
    return cur.rowcount > 0


def _extract_supersedes_invalidations(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    """Find decisions with a supersedes_decision_id and emit invalidated() links.

    A new decision that supersedes a prior one is a textbook
    ``invalidated`` causal: the new fact replaces the old fact.
    """
    try:
        rows = conn.execute(
            """SELECT id, supersedes_decision_id, source_episode_id FROM decisions
               WHERE workspace_id = ? AND supersedes_decision_id IS NOT NULL""",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    n = 0
    for row in rows:
        if _upsert_causal_link(
            conn,
            workspace_id=workspace_id,
            src_kind="decision",
            src_id=row["id"],
            dst_kind="decision",
            dst_id=row["supersedes_decision_id"],
            relation="invalidated",
            evidence_episode_id=row["source_episode_id"],
        ):
            n += 1
    return n


def _extract_insight_derivations(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    """Insight -> episode ``derived_from`` links.

    Each insight stores its evidence episode ids as JSON. Emit one
    derived_from per cited episode.
    """
    try:
        rows = conn.execute(
            """SELECT id, source_episode_ids_json FROM insights
               WHERE workspace_id = ?""",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    n = 0
    for row in rows:
        try:
            episode_ids = json.loads(row["source_episode_ids_json"] or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(episode_ids, list):
            continue
        for ep_id in episode_ids:
            if not isinstance(ep_id, str) or not ep_id:
                continue
            if _upsert_causal_link(
                conn,
                workspace_id=workspace_id,
                src_kind="insight",
                src_id=row["id"],
                dst_kind="episode",
                dst_id=ep_id,
                relation="derived_from",
            ):
                n += 1
    return n


def extract_workspace(conn: sqlite3.Connection, *, workspace_id: str) -> CausalReport:
    """Run both extractors for one workspace. Idempotent."""
    invalidated = _extract_supersedes_invalidations(conn, workspace_id=workspace_id)
    derived = _extract_insight_derivations(conn, workspace_id=workspace_id)
    conn.commit()
    return CausalReport(
        workspaces_scanned=1,
        invalidated_links=invalidated,
        derived_links=derived,
    )


def list_outgoing(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    src_kind: str,
    src_id: str,
    relation: str | None = None,
) -> list[sqlite3.Row]:
    """Read the outgoing causal links for one source. Used by recall."""
    if relation:
        sql = (
            "SELECT * FROM causal_links "
            "WHERE workspace_id = ? AND src_kind = ? AND src_id = ? "
            "AND relation = ?"
        )
        params: tuple[str, ...] = (workspace_id, src_kind, src_id, relation)
    else:
        sql = "SELECT * FROM causal_links WHERE workspace_id = ? AND src_kind = ? AND src_id = ?"
        params = (workspace_id, src_kind, src_id)
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
