"""GET /memory/telemetry — search/write rate metrics from audit_log.

Added in 1.2.4 to give operators an objective way to measure whether the
seed search-discipline behavior_instruction (1.2.4) actually shifted
agent behaviour. The route is read-only, aggregates over ``audit_log``,
and never mutates state.

Counts:
* ``search_total`` — number of read operations the agent performed in
  the window. Defined as audit rows with action in
  ``SEARCH_ACTIONS`` (search, get_context, list_decisions, list_theories,
  list_behavior_instructions, list_audit, list_candidates,
  list_research_agenda, list_agent_capabilities, list_capability_links,
  list_maintenance_events, get_object, what_references, explain_context).
* ``write_total`` — number of mutating operations
  (write_decision, write_theory, ingest_episode, ingest_file,
  upsert_*, archive, link_capability, etc — anything not read).
* ``search_per_write_ratio`` — search_total / max(write_total, 1).
* ``per_day`` — list of {date, search, write} sorted ascending.

Operator interpretation:
  ratio < 0.5 → agent writes more than it reads. Discipline gap.
  ratio 0.5 - 1.5 → balanced.
  ratio > 1.5 → search-heavy (Codex-like or genuinely investigation
  session).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable

router = APIRouter()

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


class TelemetryDayRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    search: int
    write: int


class TelemetryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    since: str
    until: str
    search_total: int
    write_total: int
    search_per_write_ratio: float
    per_day: list[TelemetryDayRow]
    by_action_top: list[dict[str, Any]]


@router.get("/memory/telemetry", response_model=TelemetryResponse)
def telemetry_route(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(default="default"),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
) -> TelemetryResponse:
    """Aggregate audit_log counts for one workspace.

    ``since`` / ``until`` are ISO-8601 inclusive endpoints. When
    ``since`` is omitted the range is ``[now - days, now]``.
    """
    ensure_workspace_readable(workspace_id, settings)
    now = datetime.now(UTC)
    since_iso = since or (now - timedelta(days=days)).isoformat()
    until_iso = until or now.isoformat()

    rows = conn.execute(
        """
        SELECT action, created_at FROM audit_log
        WHERE workspace_id = ?
          AND created_at >= ?
          AND created_at <= ?
        """,
        (workspace_id, since_iso, until_iso),
    ).fetchall()

    by_day_search: dict[str, int] = defaultdict(int)
    by_day_write: dict[str, int] = defaultdict(int)
    by_action: dict[str, int] = defaultdict(int)
    search_total = 0
    write_total = 0
    for row in rows:
        action = str(row["action"] or "")
        if action in BOOKKEEPING_ACTIONS:
            continue
        day = str(row["created_at"] or "")[:10]
        by_action[action] += 1
        if action in SEARCH_ACTIONS:
            search_total += 1
            by_day_search[day] += 1
        else:
            write_total += 1
            by_day_write[day] += 1

    per_day = [
        TelemetryDayRow(date=d, search=by_day_search.get(d, 0), write=by_day_write.get(d, 0))
        for d in sorted(set(by_day_search) | set(by_day_write))
    ]
    by_action_top = sorted(
        ({"action": k, "count": v} for k, v in by_action.items()),
        key=lambda x: x["count"],
        reverse=True,
    )[:15]
    ratio = search_total / max(write_total, 1)
    return TelemetryResponse(
        workspace_id=workspace_id,
        since=since_iso,
        until=until_iso,
        search_total=search_total,
        write_total=write_total,
        search_per_write_ratio=round(ratio, 3),
        per_day=per_day,
        by_action_top=by_action_top,
    )
