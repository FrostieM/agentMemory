"""Age-based row retention for the unbounded brain-pass tables.

Four tables grow monotonically and were never pruned: audit_log (the append-only
journal), memory_usage_feedback (implicit telemetry), causal_links (the
auto-re-derived relations), and episodes (95%+ are superseded ``file_indexed``
re-index leftovers). This module age-prunes each, scoped to one workspace.

DELETION SAFETY -- FK enforcement is OFF on these connections, so every prune is
SELF-GUARDED and never relies on a cascade:

* audit_log / memory_usage_feedback: nothing references them; age cutoff only.
* causal_links: only the mechanically re-derived relations (``derived_from`` /
  ``semantically_similar_to``, rebuilt every pass by the causal step) are pruned;
  the asserted/invalidation markers (``caused`` / ``invalidated``) are kept.
* episodes: ONLY ``source_type='file_indexed'`` rows (never agent_action /
  user_message / summary / ... -- the substantive memory), and only those with no
  surviving reference. The reference guard is a ``NOT EXISTS`` over every
  catalog-discovered episode reference, of BOTH shapes: scalar FK columns
  (``source_episode_id`` / ``evidence_episode_id`` / ``episode_id``, joined
  ``= episodes.id``) AND JSON-array columns (``*episode_ids_json``, e.g.
  ``insights.source_episode_ids_json``, scanned with ``json_each`` -- a scalar
  join cannot see those ids, so a JSON-only reference would otherwise orphan the
  citing insight). Discovered from the live catalog so a new referencing column
  of either shape is covered; if scalar discovery yields nothing the prune is
  skipped (fail-safe). Because each file ingest also writes an ``ingest_file``
  audit row pointing at its episode, a file_indexed episode only becomes prunable
  once that audit row has itself aged out -- so no reference ever dangles
  (audit_log is pruned first, in the same pass).

Every prune is bounded by ``retention_max_per_table_per_pass`` and is independent
+ failure-soft, so a guard regression on one table cannot corrupt the others.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from agent_memory_lite.utils.time import parse_iso

if TYPE_CHECKING:
    from agent_memory_lite.config.settings import Settings

# Columns by which other tables reference episodes.id (used for catalog discovery).
_EPISODE_REF_COLUMNS = ("source_episode_id", "evidence_episode_id", "episode_id")
# causal_links relations that the causal step mechanically re-derives each pass.
_REDERIVED_CAUSAL_RELATIONS = ("derived_from", "semantically_similar_to")


@dataclass
class RetentionReport:
    """Per-table prune counts + soft errors for one retention pass."""

    audit_rows_pruned: int = 0
    usage_feedback_pruned: int = 0
    causal_links_pruned: int = 0
    file_episodes_pruned: int = 0
    errors: list[str] = field(default_factory=list)


def _cutoff(now_iso: str, days: int) -> str:
    """ISO cutoff = now - days. Returns "" on a bad timestamp, which makes the
    ``created_at < ?`` predicate match nothing (a safe no-op, never a mass delete).
    """
    try:
        return (parse_iso(now_iso) - timedelta(days=max(0, days))).isoformat()
    except (TypeError, ValueError):
        return ""


def _bounded_delete(
    conn: sqlite3.Connection,
    *,
    table: str,
    where: str,
    params: tuple[object, ...],
    cap: int,
) -> int:
    """DELETE rows matching ``where`` from ``table``, at most ``cap`` per call.

    Bounded via ``id IN (SELECT id ... LIMIT cap)`` so a pathological backlog
    cannot delete an unbounded amount in one pass. ``table`` / ``where`` are
    built only from in-module string constants and live-catalog identifiers --
    never from caller/agent input.
    """
    sql = f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE {where} LIMIT ?)"
    cur = conn.execute(sql, (*params, cap))
    return cur.rowcount or 0


def _episode_referencing_pairs(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(table, column) pairs that reference ``episodes.id``, from the live catalog.

    Discovered rather than hardcoded so the episode prune guard can never miss a
    referencing table as the schema evolves.
    """
    pairs: list[tuple[str, str]] = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'episodes'"
    ).fetchall()
    for (table,) in rows:
        try:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")}
        except sqlite3.Error:
            continue
        pairs.extend((table, col) for col in _EPISODE_REF_COLUMNS if col in cols)
    return pairs


def _episode_json_referencing_pairs(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(table, column) pairs that reference episodes via a JSON ARRAY of ids --
    columns whose name ends with ``episode_ids_json`` (today only
    ``insights.source_episode_ids_json``, which consolidation fills with
    file_indexed episode ids).

    These hold a JSON list, not a scalar FK. ``_episode_referencing_pairs``
    matches only the exact scalar names in ``_EPISODE_REF_COLUMNS`` and so does
    NOT find them, and a scalar ``= episodes.id`` join cannot read array elements
    -- hence this separate suffix-based discovery, feeding a ``json_each`` guard.
    Discovered by suffix so a future such column is covered automatically.
    """
    pairs: list[tuple[str, str]] = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'episodes'"
    ).fetchall()
    for (table,) in rows:
        try:
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")]
        except sqlite3.Error:
            continue
        pairs.extend((table, col) for col in cols if col.endswith("episode_ids_json"))
    return pairs


def prune_retention(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    settings: Settings,
    now_iso: str,
    cap: int,
) -> RetentionReport:
    """Age-prune the four unbounded tables for one workspace. Failure-soft."""
    report = RetentionReport()

    # 1. audit_log -- pure history, nothing references it. Pruned FIRST so a
    #    file_indexed episode whose only remaining reference is an aged-out
    #    ingest_file audit row becomes prunable in step 4 of the same pass.
    try:
        report.audit_rows_pruned = _bounded_delete(
            conn,
            table="audit_log",
            where="workspace_id = ? AND created_at < ?",
            params=(workspace_id, _cutoff(now_iso, settings.retention_audit_days)),
            cap=cap,
        )
    except sqlite3.Error as exc:
        report.errors.append(f"audit_log:{exc}")

    # 2. memory_usage_feedback -- implicit telemetry, no incoming references.
    try:
        report.usage_feedback_pruned = _bounded_delete(
            conn,
            table="memory_usage_feedback",
            where="workspace_id = ? AND created_at < ?",
            params=(workspace_id, _cutoff(now_iso, settings.retention_usage_feedback_days)),
            cap=cap,
        )
    except sqlite3.Error as exc:
        report.errors.append(f"memory_usage_feedback:{exc}")

    # 3. causal_links -- only the auto-re-derived relations, by age. Asserted /
    #    invalidation markers are never auto-rebuilt, so they are kept.
    try:
        placeholders = ", ".join("?" for _ in _REDERIVED_CAUSAL_RELATIONS)
        report.causal_links_pruned = _bounded_delete(
            conn,
            table="causal_links",
            where=f"workspace_id = ? AND relation IN ({placeholders}) AND created_at < ?",
            params=(
                workspace_id,
                *_REDERIVED_CAUSAL_RELATIONS,
                _cutoff(now_iso, settings.retention_causal_days),
            ),
            cap=cap,
        )
    except sqlite3.Error as exc:
        report.errors.append(f"causal_links:{exc}")

    # 4. episodes -- superseded file_indexed re-index leftovers only, gated on a
    #    NOT EXISTS over every catalog-discovered episode-referencing table:
    #    scalar FK columns (= episodes.id) AND JSON-array columns (json_each), the
    #    latter because insights.source_episode_ids_json cites file_indexed
    #    episodes invisibly to a scalar join. Both must be guarded or the prune
    #    would orphan an insight's provenance.
    try:
        pairs = _episode_referencing_pairs(conn)
        if pairs:  # fail-safe: no discovered referencing table -> never prune
            scalar_guard = "".join(
                f" AND NOT EXISTS (SELECT 1 FROM {t} WHERE {t}.{c} = episodes.id)" for t, c in pairs
            )
            # Fail-CLOSED on malformed JSON: if any insight's source_episode_ids_json
            # is not valid JSON, json_each raises and the whole episode prune is
            # skipped this pass (caught below -> reported, no deletion). That is the
            # safe posture for a delete -- an unreadable citation must never let an
            # episode be deleted. The column is NOT NULL DEFAULT '[]' and written
            # via json.dumps, so this is effectively unreachable in normal operation.
            json_guard = "".join(
                f" AND NOT EXISTS (SELECT 1 FROM {t} jt, json_each(jt.{c}) je "
                f"WHERE jt.workspace_id = episodes.workspace_id AND je.value = episodes.id)"
                for t, c in _episode_json_referencing_pairs(conn)
            )
            report.file_episodes_pruned = _bounded_delete(
                conn,
                table="episodes",
                where=(
                    "workspace_id = ? AND source_type = 'file_indexed' AND created_at < ?"
                    f"{scalar_guard}{json_guard}"
                ),
                params=(workspace_id, _cutoff(now_iso, settings.retention_file_episode_days)),
                cap=cap,
            )
    except sqlite3.Error as exc:
        report.errors.append(f"episodes:{exc}")

    try:
        conn.commit()
    except sqlite3.Error as exc:
        report.errors.append(f"commit:{exc}")
    return report
