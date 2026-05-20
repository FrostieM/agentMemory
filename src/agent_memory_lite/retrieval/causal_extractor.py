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


# Jaccard threshold above which two decision titles are treated as the
# same content (metadata refresh) rather than a semantic invalidation.
# Empirically tuned on copyBot: a 3-token old title with a 2-token
# refresh prefix yields Jaccard 0.6; real architectural pivots ('PR1
# architectural realignment ...' vs 'Layer 1.5 deployed ...') yield
# < 0.2. 0.5 sits comfortably in the gap and catches every refresh
# pattern observed without producing any false-positive on real pivots.
_SIMILARITY_SUPPRESS_THRESHOLD = 0.5


_PUNCT_STRIP = ".,;:!?()[]{}\"'`"


def _title_tokens(title: str | None) -> frozenset[str]:
    """Tokenize a title for similarity comparison.

    Lowercase, whitespace-split, strip surrounding punctuation so
    ``refresh:`` and ``refresh`` count as the same token, drop very
    short tokens (<= 2 chars) since they're mostly stopwords and noise.
    Returns frozenset for cheap set ops.
    """
    if not title:
        return frozenset()
    cleaned: list[str] = []
    for raw in title.lower().split():
        tok = raw.strip(_PUNCT_STRIP)
        if len(tok) > 2:
            cleaned.append(tok)
    return frozenset(cleaned)


def _titles_too_similar(new: str | None, old: str | None) -> bool:
    """Return True if ``new`` is just a metadata refresh of ``old``.

    Jaccard similarity on lowercase tokens. Above the threshold, the
    new decision shares so much vocabulary with the old that emitting
    an ``invalidated`` causal link would be misleading -- it's the
    same content with a refresh prefix, not a real semantic
    contradiction. Below the threshold, the two decisions represent
    different positions and the ``invalidated`` link is correct.
    """
    a = _title_tokens(new)
    b = _title_tokens(old)
    if not a or not b:
        return False
    union = len(a | b)
    if union == 0:
        return False
    return (len(a & b) / union) >= _SIMILARITY_SUPPRESS_THRESHOLD


def _extract_supersedes_invalidations(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    """Find decisions with a supersedes_decision_id and emit invalidated() links.

    A new decision that supersedes a prior one is USUALLY a textbook
    ``invalidated`` causal: the new fact replaces the old fact. The
    exception is "metadata refresh" supersedes where the new decision
    has nearly the same title as the old one (e.g.
    "Provenance refresh: X" -> "X"). Those represent the same content
    re-versioned, not a semantic contradiction, and would mislead
    recall callers into thinking the old position was overturned.

    The Jaccard-based gate suppresses those; the remaining supersedes
    are real invalidations.
    """
    try:
        rows = conn.execute(
            """SELECT d_new.id AS new_id,
                      d_new.title AS new_title,
                      d_new.source_episode_id AS source_episode_id,
                      d_old.id AS old_id,
                      d_old.title AS old_title
                 FROM decisions d_new
                 JOIN decisions d_old
                   ON d_new.supersedes_decision_id = d_old.id
                WHERE d_new.workspace_id = ?
                  AND d_old.workspace_id = ?""",
            (workspace_id, workspace_id),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    n = 0
    for row in rows:
        if _titles_too_similar(row["new_title"], row["old_title"]):
            continue  # metadata refresh, not invalidation
        if _upsert_causal_link(
            conn,
            workspace_id=workspace_id,
            src_kind="decision",
            src_id=row["new_id"],
            dst_kind="decision",
            dst_id=row["old_id"],
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


def list_outgoing_batch(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    src_kind: str,
    src_ids: list[str],
) -> dict[str, list[sqlite3.Row]]:
    """Round-2 audit: batched ``list_outgoing`` for the recall hot path.

    Replaces N per-source SELECTs with one IN-list query when callers
    already have a list of src_ids of the same src_kind. Returns a
    dict mapping ``src_id`` -> rows so callers can fan out without
    extra round-trips. Empty input -> empty dict. Missing
    causal_links table (pre-migration DB) -> empty dict (failure-soft,
    matches ``list_outgoing``).
    """
    if not src_ids:
        return {}
    unique = list(dict.fromkeys(src_ids))[:500]
    placeholders = ",".join("?" * len(unique))
    sql = (
        "SELECT * FROM causal_links WHERE workspace_id = ? "
        f"AND src_kind = ? AND src_id IN ({placeholders})"
    )
    try:
        rows = conn.execute(sql, (workspace_id, src_kind, *unique)).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, list[sqlite3.Row]] = {sid: [] for sid in unique}
    for row in rows:
        out.setdefault(row["src_id"], []).append(row)
    return out


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
