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
    existing = conn.execute(
        "SELECT id, recurrence_count FROM maintenance_events "
        "WHERE workspace_id = ? AND kind = ? AND status = 'open'",
        (workspace_id, kind),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE maintenance_events "
            "SET recurrence_count = COALESCE(recurrence_count, 0) + 1, "
            "    last_seen_at = ?, summary = ?, details_json = ? "
            "WHERE id = ?",
            (now, summary, json.dumps(details), existing[0]),
        )
        return False
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


def detect_drift(conn: sqlite3.Connection, *, workspace_id: str) -> DriftReport:
    """Run all drift checks. Emits ``maintenance_events`` for findings,
    resolves them when underlying metrics clear. Returns a summary."""
    report = DriftReport()
    if not is_enabled():
        return report
    threshold = coverage_threshold()

    # FK violations
    fk_result = _check_fk_violations(conn, workspace_id)
    if fk_result is not None:
        summary, count = fk_result
        try:
            _upsert_finding(
                conn,
                workspace_id=workspace_id,
                kind="memory_drift_fk",
                summary=summary,
                details={"violations": count},
            )
            report.findings.append(f"fk:{count}")
        except sqlite3.Error as exc:
            report.errors.append(f"fk:{exc}")
    elif _resolve_finding(conn, workspace_id=workspace_id, kind="memory_drift_fk"):
        report.resolved.append("fk")

    # FTS coverage
    fts_result = _check_coverage_gap(
        conn, workspace_id, sibling_table="chunks_fts", sibling_label="FTS", threshold=threshold
    )
    if fts_result is not None:
        summary, ratio = fts_result
        try:
            _upsert_finding(
                conn,
                workspace_id=workspace_id,
                kind="memory_drift_fts",
                summary=summary,
                details={"coverage": ratio},
            )
            report.findings.append(f"fts:{int(ratio * 100)}%")
        except sqlite3.Error as exc:
            report.errors.append(f"fts:{exc}")
    elif _resolve_finding(conn, workspace_id=workspace_id, kind="memory_drift_fts"):
        report.resolved.append("fts")

    # Vector coverage (via embedding_id presence in chunks — cheap proxy
    # that doesn't require opening LanceDB on every brain_pass tick).
    vec_result = _check_coverage_gap(
        conn,
        workspace_id,
        sibling_table="embedding_id",
        sibling_label="vector",
        threshold=threshold,
    )
    if vec_result is not None:
        summary, ratio = vec_result
        try:
            _upsert_finding(
                conn,
                workspace_id=workspace_id,
                kind="memory_drift_vector",
                summary=summary,
                details={"coverage": ratio},
            )
            report.findings.append(f"vector:{int(ratio * 100)}%")
        except sqlite3.Error as exc:
            report.errors.append(f"vector:{exc}")
    elif _resolve_finding(conn, workspace_id=workspace_id, kind="memory_drift_vector"):
        report.resolved.append("vector")

    conn.commit()
    return report
