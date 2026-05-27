"""v3.4 drift sentinel — auto-detect memory drift between aud runs.

Today's audit on copyBot found 2271 FK violations, 67% FTS coverage
gap, and 50% vector index gap that had accumulated silently over
months. Nobody noticed because nobody ran ``memory_audit.py`` — it's
opt-in operator tooling. By the time the gap was found, semantic
search was effectively broken for two-thirds of the corpus.

This module makes drift visible by detecting it on every brain_pass
tick and persisting a ``maintenance_event`` per dimension that
crosses a threshold. The operator (or the upcoming hygiene action
queue, v3.4 item #6) then triages the events.

Checks shipped:

* ``fk_violations`` — chunks with dangling file_id refs.
  Threshold: any > 0 (each one = a broken pointer).
* ``fts_coverage`` — chunks_fts row count vs chunks row count.
  Threshold: < 90% triggers warning.
* ``vector_coverage`` — vectors row count vs chunks row count.
  Threshold: < 90% triggers warning.
* ``orphan_chunks_growth`` — chunks where file_id is NOT NULL and
  the file row has been deleted. Same query as fk_violations but
  scoped to the workspace; pinned separately so v3.5 can act on it
  without scanning the FK matrix.
* ``dangling_capability_links`` — capability_links rows whose
  (capability_type, capability_id) target no longer exists in
  skills(subtype='role'/'skill'/'playbook'). The schema has no
  FK on (capability_type, capability_id) so deleting a capability
  leaves dangling refs that PRAGMA foreign_key_check cannot see;
  today (2026-05-20) we hand-cleaned 16 such orphans that had
  accumulated since 2026-05-10. This check catches them the same
  brain_pass tick they appear (theory th_6bbb2cf024961a0f).

Each finding lands as ``maintenance_event(kind='memory_drift', ...)``
with a stable id derived from (workspace, check). Re-running the
sentinel just increments ``recurrence_count`` and updates
``last_seen_at`` — no duplicate rows accumulate. When the underlying
metric clears, the event is resolved automatically.

Failure-soft: missing tables (pre-migration DBs) cause individual
checks to skip without exception so brain_pass keeps moving.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


@dataclass(slots=True)
class DriftReport:
    """Per-workspace drift detection summary."""

    findings: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


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
    """v3.4: default ON. ``MEMORY_DRIFT_SENTINEL_ENABLED=false`` to
    opt out (the audit script still works, just no auto-detection)."""
    return _bool_env("MEMORY_DRIFT_SENTINEL_ENABLED", True)


def coverage_threshold() -> float:
    """FTS / vector coverage must stay above this fraction. Default
    0.90 — anything missing more than 10% of chunks needs attention."""
    return _float_env("MEMORY_DRIFT_COVERAGE_MIN", 0.90)


def _check_fk_violations(conn: sqlite3.Connection, workspace_id: str) -> tuple[str, int] | None:
    """Count chunks referencing missing files in this workspace.
    Returns ``(detail, count)`` when count > 0, ``None`` when clean."""
    try:
        row = conn.execute(
            """SELECT COUNT(*) FROM chunks c
                 LEFT JOIN files f ON f.id = c.file_id
                WHERE c.workspace_id = ?
                  AND c.file_id IS NOT NULL
                  AND f.id IS NULL""",
            (workspace_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    count = int(row[0]) if row else 0
    if count == 0:
        return None
    return (
        f"{count} chunk(s) reference missing files. "
        f"Run scripts/memory_audit.py --repair-vectors to clean.",
        count,
    )


def _check_dangling_capability_links(
    conn: sqlite3.Connection, workspace_id: str
) -> tuple[str, int] | None:
    """Count capability_links whose (capability_type, capability_id) no
    longer resolves to a canonical skills row in this
    workspace. Returns ``(detail, count)`` when count > 0, ``None`` when
    clean.

    SQLite has no FK on (capability_type, capability_id) — see the
    schema for capability_links — so deleting a capability silently
    leaves dangling refs. Today's audit had 16 such rows pointing at
    play_badf081489e59f36 / skill_81b73a2284f20e6a, both deleted by an
    earlier reseed without redirecting links to the surviving same-name
    twins. This check would have caught them at the next brain_pass."""
    try:
        row = conn.execute(
            """SELECT COUNT(*) FROM capability_links cl
                 LEFT JOIN skills s
                        ON s.id = cl.capability_id
                       AND s.workspace_id = cl.workspace_id
                       AND s.subtype = cl.capability_type
                WHERE cl.workspace_id = ?
                  AND cl.capability_type IN ('role', 'skill', 'playbook')
                  AND s.id IS NULL""",
            (workspace_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    count = int(row[0]) if row else 0
    if count == 0:
        return None
    return (
        f"{count} capability_link(s) point at a deleted role/skill/playbook. "
        f"Inspect via scripts/memory_audit.py or re-target to a "
        f"surviving same-name twin.",
        count,
    )


def _check_coverage_gap(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    sibling_table: str,
    sibling_label: str,
    threshold: float,
) -> tuple[str, float] | None:
    """Generic coverage check: sibling row count vs chunks row count.

    ``sibling_table`` is the index table (chunks_fts) — for vector
    coverage we read it from chunks.embedding_id NOT NULL instead
    because LanceDB lives outside SQLite.
    """
    try:
        chunks = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return None
    if chunks == 0:
        return None
    if sibling_table == "chunks_fts":
        try:
            sibling = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        except sqlite3.OperationalError:
            return None
    else:  # embedding_id presence acts as vector-coverage proxy
        try:
            sibling = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE workspace_id = ? AND embedding_id IS NOT NULL",
                (workspace_id,),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            return None
    coverage = sibling / chunks
    if coverage >= threshold:
        return None
    pct = int(coverage * 100)
    return (
        f"{sibling_label} coverage at {pct}% ({sibling}/{chunks}). "
        f"Below {int(threshold * 100)}% threshold. "
        f"Run scripts/memory_audit.py --repair-vectors --repair-fts.",
        coverage,
    )


_DETECTORS = (
    ("memory_drift_fk", _check_fk_violations, "FK violations"),
    # Coverage checks are wrapped via a small lambda layer to bind
    # extra kwargs without losing the homogeneous detector signature.
)


def _upsert_finding(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    summary: str,
    details: dict[str, object],
) -> bool:
    """Idempotent: same (workspace, kind) merges into existing row by
    bumping ``recurrence_count`` and refreshing ``last_seen_at``.
    Returns True if a new row was inserted (vs a recurrence bump)."""
    now = iso_now()
    # v3.5 sector-5 audit-followup: look for ANY existing row for this
    # (workspace, kind) — not just open ones. The pre-fix code only
    # found open rows; a resolved row from a prior tick that re-trips
    # would INSERT a fresh row, leaving stale "resolved" rows
    # accumulating and resetting recurrence_count to 1 each time.
    # Now we re-open the same row instead.
    existing = conn.execute(
        "SELECT id, recurrence_count, status FROM maintenance_events "
        "WHERE workspace_id = ? AND kind = ? "
        "ORDER BY last_seen_at DESC LIMIT 1",
        (workspace_id, kind),
    ).fetchone()
    if existing:
        prior_status = str(existing[2] or "open")
        was_resolved = prior_status == "resolved"
        conn.execute(
            "UPDATE maintenance_events "
            "SET status = 'open', "
            "    recurrence_count = COALESCE(recurrence_count, 0) + 1, "
            "    last_seen_at = ?, summary = ?, details_json = ?, "
            "    resolved_at = NULL "
            "WHERE id = ?",
            (now, summary, json.dumps(details), existing[0]),
        )
        # Treat a resolve→reopen as a fresh emit for callers that
        # track new-vs-recurrence (so the operator sees the regression
        # rather than just a silent counter bump).
        return was_resolved
    conn.execute(
        """INSERT INTO maintenance_events
           (id, workspace_id, kind, severity, status, summary,
            details_json, created_at, first_seen_at, last_seen_at,
            recurrence_count)
           VALUES (?, ?, ?, 'warning', 'open', ?, ?, ?, ?, ?, 1)""",
        (new_id(IdKind.AUDIT), workspace_id, kind, summary, json.dumps(details), now, now, now),
    )
    return True


def _resolve_finding(conn: sqlite3.Connection, *, workspace_id: str, kind: str) -> bool:
    """Mark a previously-open finding resolved when the underlying
    metric cleared. Returns True if a row was flipped."""
    now = iso_now()
    cur = conn.execute(
        "UPDATE maintenance_events SET status = 'resolved', resolved_at = ? "
        "WHERE workspace_id = ? AND kind = ? AND status = 'open'",
        (now, workspace_id, kind),
    )
    return cur.rowcount > 0


def _emit_or_resolve(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    short_label: str,
    result: tuple[str, float | int] | None,
    detail_key: str,
    finding_fmt: Callable[[float | int], str],
    report: DriftReport,
) -> None:
    """Common landing path for every detector: upsert finding when the
    check returned non-None, otherwise resolve any prior open event.

    Factored out so ``detect_drift`` stays under ruff's PLR0912 branch
    budget when we add more checks (we already have four; #6 hygiene
    action queue + future referent checks will keep adding more)."""
    if result is not None:
        summary, value = result
        try:
            _upsert_finding(
                conn,
                workspace_id=workspace_id,
                kind=kind,
                summary=summary,
                details={detail_key: value},
            )
            report.findings.append(finding_fmt(value))
        except sqlite3.Error as exc:
            report.errors.append(f"{short_label}:{exc}")
    elif _resolve_finding(conn, workspace_id=workspace_id, kind=kind):
        report.resolved.append(short_label)


def detect_drift(conn: sqlite3.Connection, *, workspace_id: str) -> DriftReport:
    """Run all drift checks. Emits ``maintenance_events`` for findings,
    resolves them when underlying metrics clear. Returns a summary."""
    report = DriftReport()
    if not is_enabled():
        return report
    threshold = coverage_threshold()

    _emit_or_resolve(
        conn,
        workspace_id=workspace_id,
        kind="memory_drift_fk",
        short_label="fk",
        result=_check_fk_violations(conn, workspace_id),
        detail_key="violations",
        finding_fmt=lambda v: f"fk:{int(v)}",
        report=report,
    )
    # Dangling capability_links — schema has no FK on (capability_type,
    # capability_id), so this check is the only path that catches them
    # before the operator runs memory_audit (theory th_6bbb2cf024961a0f).
    _emit_or_resolve(
        conn,
        workspace_id=workspace_id,
        kind="memory_drift_capability_links",
        short_label="capability_links",
        result=_check_dangling_capability_links(conn, workspace_id),
        detail_key="dangling",
        finding_fmt=lambda v: f"capability_links:{int(v)}",
        report=report,
    )
    _emit_or_resolve(
        conn,
        workspace_id=workspace_id,
        kind="memory_drift_fts",
        short_label="fts",
        result=_check_coverage_gap(
            conn,
            workspace_id,
            sibling_table="chunks_fts",
            sibling_label="FTS",
            threshold=threshold,
        ),
        detail_key="coverage",
        finding_fmt=lambda v: f"fts:{int(float(v) * 100)}%",
        report=report,
    )
    # Vector coverage via chunks.embedding_id presence — cheap proxy
    # that doesn't require opening LanceDB on every brain_pass tick.
    _emit_or_resolve(
        conn,
        workspace_id=workspace_id,
        kind="memory_drift_vector",
        short_label="vector",
        result=_check_coverage_gap(
            conn,
            workspace_id,
            sibling_table="embedding_id",
            sibling_label="vector",
            threshold=threshold,
        ),
        detail_key="coverage",
        finding_fmt=lambda v: f"vector:{int(float(v) * 100)}%",
        report=report,
    )

    conn.commit()
    return report
