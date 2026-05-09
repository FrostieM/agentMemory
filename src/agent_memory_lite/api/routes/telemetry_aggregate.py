"""Aggregation helpers for /memory/telemetry (1.3.0).

Split from ``telemetry.py`` to keep the route module under the
150-SLOC ceiling and to make the partition logic unit-testable
without going through the full FastAPI route.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# Read-side actions tracked as "search". Anything not in this set and
# not in the bookkeeping set below is counted as a write.
SEARCH_ACTIONS: frozenset[str] = frozenset(
    {
        "search",
        "get_context",
        "explain_context",
        "list_decisions",
        "list_theories",
        "list_behavior_instructions",
        "list_audit",
        "list_candidates",
        "list_research_agenda",
        "list_agent_capabilities",
        "list_capability_links",
        "list_maintenance_events",
        "get_object",
        "what_references",
        "memory_list_decisions",
        "memory_list_theories",
        "memory_list_candidates",
    }
)

# Bookkeeping / passive events excluded from both counts so they
# don't skew the ratio. Mostly internal sentinel + telemetry traces.
BOOKKEEPING_ACTIONS: frozenset[str] = frozenset(
    {
        "sentinel.run_recorded",
        "ui_event",
        "memory_get_context",  # already counted as get_context
    }
)


@dataclass
class TelemetryAggregate:
    by_day_search: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_day_write: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_action: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_agent_search: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_agent_write: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    search_total: int = 0
    write_total: int = 0
    search_calls_with_hits: int = 0
    search_calls_zero_hits: int = 0


def fetch_audit_rows(
    conn: sqlite3.Connection, *, workspace_id: str, since_iso: str, until_iso: str
) -> list[Any]:
    """Select audit rows for the window. Falls back to a query without
    ``agent_id`` when migration 0026 has not been applied yet."""
    try:
        return conn.execute(
            """
            SELECT action, created_at, agent_id, after_json FROM audit_log
            WHERE workspace_id = ?
              AND created_at >= ?
              AND created_at <= ?
            """,
            (workspace_id, since_iso, until_iso),
        ).fetchall()
    except sqlite3.OperationalError:
        return conn.execute(
            """
            SELECT action, created_at, NULL AS agent_id, after_json FROM audit_log
            WHERE workspace_id = ?
              AND created_at >= ?
              AND created_at <= ?
            """,
            (workspace_id, since_iso, until_iso),
        ).fetchall()


def _bump_hit_rate(after_raw: object, agg: TelemetryAggregate) -> None:
    if not after_raw:
        return
    if not isinstance(after_raw, (str, bytes, bytearray)):
        return
    try:
        after = json.loads(after_raw)
        if not isinstance(after, dict):
            return
        hits = int(after.get("hits") or after.get("sources") or 0)
        if hits > 0:
            agg.search_calls_with_hits += 1
        else:
            agg.search_calls_zero_hits += 1
    except (ValueError, TypeError):
        pass


def aggregate_audit_rows(rows: list[Any]) -> TelemetryAggregate:
    """Walk audit rows once and partition into search/write/agent/day."""
    agg = TelemetryAggregate()
    for row in rows:
        action = str(row["action"] or "")
        if action in BOOKKEEPING_ACTIONS:
            continue
        day = str(row["created_at"] or "")[:10]
        agent = str(row["agent_id"] or "(unknown)")
        agg.by_action[action] += 1
        if action in SEARCH_ACTIONS:
            agg.search_total += 1
            agg.by_day_search[day] += 1
            agg.by_agent_search[agent] += 1
            _bump_hit_rate(row["after_json"], agg)
        else:
            agg.write_total += 1
            agg.by_day_write[day] += 1
            agg.by_agent_write[agent] += 1
    return agg
