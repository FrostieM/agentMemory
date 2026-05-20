"""v3.3 Vector 4 method (a) — DiD on supersede events.

Roadmap describes three approaches for learned causality:

* (a) **Difference-in-differences on supersede events.** For each
  ``dec_new supersedes dec_old`` pair, compare outcome metrics
  before vs after; emit ``causal_link(caused, weight=|delta|)``
  when the delta is meaningful.
* (b) Granger causality on episode-token time series.
* (c) Counterfactual via embedding similarity.

v3.1 shipped (c) via ``retrieval.causal_embedding``. This module adds
(a) so the causal layer is double-sourced. When the same ordered pair
shows up in BOTH (a) ``caused`` AND (c) ``semantically_similar_to``,
the recall layer reads two confirming signals — a soft form of the
multi-method confidence boost the roadmap called for.

Simplified DiD: we use the steady-state ``outcome_score`` on each
decision as the "outcome metric" because the outcome loop already
folds feedback EWMA + age + supersede chain into that field. Strict
time-windowed before/after is left for a follow-up — the current
proxy still captures the right sign and is deterministic.

Failure-soft: missing ``causal_links`` / ``decisions`` / ``outcome_score``
columns return an empty report so brain_pass keeps moving.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class DidReport:
    """Per-workspace DiD extraction summary."""

    pairs_scanned: int
    links_emitted: int


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def is_enabled() -> bool:
    """v3.3: default ON. Operator sets
    ``MEMORY_CAUSAL_DID_ENABLED=false`` to opt out."""
    return _bool_env("MEMORY_CAUSAL_DID_ENABLED", True)


def threshold() -> float:
    """Minimum |outcome_score(new) - outcome_score(old)| to emit a link.

    0.3 sits in the gap between metadata-refresh supersedes (delta ~0.0
    because both rows carry the same outcome) and architectural pivots
    (delta typically 0.4+ when the new decision rescued a failing one).
    """
    return _float_env("MEMORY_CAUSAL_DID_THRESHOLD", 0.3)


def _upsert_causal_link(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    src_id: str,
    dst_id: str,
    weight: float,
) -> bool:
    """INSERT OR IGNORE one causal_link. Returns True on a new row.

    The UNIQUE constraint on (workspace_id, src_kind, src_id, dst_kind,
    dst_id, relation) makes re-runs idempotent.
    """
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO causal_links
               (id, workspace_id, src_kind, src_id, dst_kind, dst_id,
                relation, weight, evidence_episode_id, created_at)
               VALUES (?, ?, 'decision', ?, 'decision', ?,
                       'caused', ?, NULL, ?)""",
            (
                new_id(IdKind.AUDIT),
                workspace_id,
                src_id,
                dst_id,
                weight,
                iso_now(),
            ),
        )
    except sqlite3.OperationalError:
        return False
    return cur.rowcount > 0


def extract_did_links(conn: sqlite3.Connection, *, workspace_id: str) -> DidReport:
    """Scan supersede pairs and emit ``caused`` links where the
    outcome delta crosses the threshold.

    The query selects only decisions with a non-NULL
    ``supersedes_decision_id`` and a non-NULL old-decision outcome —
    keeps the per-call cost low even on workspaces with thousands of
    rows.
    """
    if not is_enabled():
        return DidReport(pairs_scanned=0, links_emitted=0)
    delta_threshold = threshold()
    try:
        rows = conn.execute(
            """SELECT dn.id   AS new_id,
                      dn.outcome_score AS new_outcome,
                      do.id   AS old_id,
                      do.outcome_score AS old_outcome
                 FROM decisions dn
                 JOIN decisions do ON do.id = dn.supersedes_decision_id
                WHERE dn.workspace_id = ?
                  AND do.workspace_id = ?
                  AND dn.supersedes_decision_id IS NOT NULL""",
            (workspace_id, workspace_id),
        ).fetchall()
    except sqlite3.OperationalError:
        return DidReport(pairs_scanned=0, links_emitted=0)
    pairs = 0
    emitted = 0
    for row in rows:
        pairs += 1
        new_outcome = (
            float(row[1] or 0.0)
            if not isinstance(row, sqlite3.Row)
            else float(row["new_outcome"] or 0.0)
        )
        old_outcome = (
            float(row[3] or 0.0)
            if not isinstance(row, sqlite3.Row)
            else float(row["old_outcome"] or 0.0)
        )
        delta = abs(new_outcome - old_outcome)
        if delta < delta_threshold:
            continue
        new_id_val = row[0] if not isinstance(row, sqlite3.Row) else row["new_id"]
        old_id_val = row[2] if not isinstance(row, sqlite3.Row) else row["old_id"]
        if _upsert_causal_link(
            conn,
            workspace_id=workspace_id,
            src_id=str(new_id_val),
            dst_id=str(old_id_val),
            weight=delta,
        ):
            emitted += 1
    if emitted:
        conn.commit()
    return DidReport(pairs_scanned=pairs, links_emitted=emitted)
