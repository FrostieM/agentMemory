"""SQL helpers for ``GET /memory/status``.

Split out of ``memory_status.py`` so the route file stays at or below
the 150-SLOC ceiling. Every helper here is read-only and tolerant of
missing tables — the status endpoint is meant to survive on a fresh
DB without any migrations applied yet.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta

from agent_memory_lite.api.schemas.memory_status import (
    AdoptionRatios,
    CodeMemoryCounts,
    MemoryCounts,
)


def count_(conn: sqlite3.Connection, sql: str, *args: object) -> int:
    return int(conn.execute(sql, args).fetchone()[0] or 0)


def max_ts(conn: sqlite3.Connection, sql: str, *args: object) -> str | None:
    row = conn.execute(sql, args).fetchone()
    return row[0] if row and row[0] else None


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def safe_count(conn: sqlite3.Connection, table: str, sql_suffix: str = "", *args: object) -> int:
    if not table_exists(conn, table):
        return 0
    return count_(conn, f"SELECT COUNT(*) FROM {table} {sql_suffix}", *args)


def gather_memory_counts(conn: sqlite3.Connection, ws: str) -> MemoryCounts:
    return MemoryCounts(
        decisions_active=safe_count(
            conn, "decisions", "WHERE workspace_id=? AND status='active'", ws
        ),
        decisions_total=safe_count(conn, "decisions", "WHERE workspace_id=?", ws),
        theories_active=safe_count(
            conn, "theories", "WHERE workspace_id=? AND status NOT IN ('archived','rejected')", ws
        ),
        theories_total=safe_count(conn, "theories", "WHERE workspace_id=?", ws),
        behaviors_active=safe_count(conn, "behaviors", "WHERE workspace_id=? AND active=1", ws),
        behaviors_total=safe_count(conn, "behaviors", "WHERE workspace_id=?", ws),
        capabilities_active_total=safe_count(
            conn, "skills", "WHERE workspace_id=? AND active=1", ws
        ),
        episodes_total=safe_count(conn, "episodes", "WHERE workspace_id=?", ws),
        chunks_total=safe_count(conn, "chunks", "WHERE workspace_id=? AND is_archived=0", ws),
        insights_new=safe_count(conn, "insights", "WHERE workspace_id=? AND status='new'", ws),
        pending_candidates=safe_count(
            conn, "candidates", "WHERE workspace_id=? AND status='new'", ws
        ),
    )


def gather_code_counts(conn: sqlite3.Connection, ws: str) -> CodeMemoryCounts:
    # ``qualified_name`` and ``symbol_kind`` are optional on partial-schema
    # DBs; safe_count returns zero via introspection rather than crashing.
    return CodeMemoryCounts(
        files=safe_count(conn, "code_digests", "WHERE workspace_id=?", ws),
        chunks_with_symbols=safe_count(
            conn,
            "chunks",
            "WHERE workspace_id=? AND is_archived=0 AND qualified_name IS NOT NULL",
            ws,
        ),
        symbols=safe_count(
            conn,
            "chunks",
            "WHERE workspace_id=? AND is_archived=0 AND symbol_kind IS NOT NULL",
            ws,
        ),
        edges=safe_count(conn, "symbol_edges", "WHERE workspace_id=?", ws),
        soft_edges=safe_count(conn, "soft_edges", "WHERE workspace_id=?", ws),
        versions=safe_count(conn, "symbol_versions", "WHERE workspace_id=?", ws),
    )


def gather_adoption(conn: sqlite3.Connection, ws: str) -> AdoptionRatios:
    dec_active = safe_count(conn, "decisions", "WHERE workspace_id=? AND status='active'", ws)
    if dec_active == 0:
        return AdoptionRatios(
            decisions_with_source_episode_ratio=0.0,
            decisions_linked_to_capability_ratio=0.0,
            behaviors_fired_ratio=0.0,
        )
    dec_w_src = safe_count(
        conn,
        "decisions",
        "WHERE workspace_id=? AND status='active' AND source_episode_id IS NOT NULL",
        ws,
    )
    dec_linked = (
        count_(
            conn,
            "SELECT COUNT(DISTINCT target_id) FROM capability_links "
            "WHERE workspace_id=? AND target_type='decision'",
            ws,
        )
        if table_exists(conn, "capability_links")
        else 0
    )
    beh_active = safe_count(conn, "behaviors", "WHERE workspace_id=? AND active=1", ws)
    beh_fired = (
        safe_count(
            conn,
            "behaviors",
            "WHERE workspace_id=? AND active=1 AND application_count > 0",
            ws,
        )
        if beh_active
        else 0
    )
    return AdoptionRatios(
        decisions_with_source_episode_ratio=round(dec_w_src / dec_active, 3),
        decisions_linked_to_capability_ratio=round(dec_linked / dec_active, 3),
        behaviors_fired_ratio=(round(beh_fired / beh_active, 3) if beh_active else 0.0),
    )


def recent_actions_7d(conn: sqlite3.Connection, ws: str) -> dict[str, int]:
    if not table_exists(conn, "audit_log"):
        return {}
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    rows = conn.execute(
        "SELECT action FROM audit_log WHERE workspace_id=? AND created_at>=?",
        (ws, cutoff),
    ).fetchall()
    return dict(Counter(r[0] for r in rows).most_common(8))
