"""GET /memory/telemetry — search/write rate metrics from audit_log.

Added in 1.2.4 to give operators an objective way to measure whether the
seed search-discipline behavior_instruction (1.2.4) actually shifted
agent behaviour. The route is read-only, aggregates over ``audit_log``,
and never mutates state.

1.3.0 additions:
* ``by_agent`` partition (Claude vs Codex vs hub-mcp vs scripts) keyed
  off the ``agent_id`` column from migration 0026.
* ``search_hit_rate`` derived from ``after_json.hits`` (search route)
  and ``after_json.sources`` (get_context route) — surfaces the rate
  of empty searches.

Operator interpretation:
  ratio < 0.5 → agent writes more than it reads. Discipline gap.
  ratio 0.5 - 1.5 → balanced.
  ratio > 1.5 → search-heavy (Codex-like or genuinely investigation).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.telemetry_aggregate import (
    aggregate_audit_rows,
    fetch_audit_rows,
)

router = APIRouter()


class TelemetryDayRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    search: int
    write: int


class AgentRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    search: int
    write: int
    ratio: float


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
    by_agent: list[AgentRow] = []
    search_calls_with_hits: int = 0
    search_calls_zero_hits: int = 0
    search_hit_rate: float = 0.0


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

    rows = fetch_audit_rows(
        conn, workspace_id=workspace_id, since_iso=since_iso, until_iso=until_iso
    )
    agg = aggregate_audit_rows(rows)

    per_day = [
        TelemetryDayRow(
            date=d,
            search=agg.by_day_search.get(d, 0),
            write=agg.by_day_write.get(d, 0),
        )
        for d in sorted(set(agg.by_day_search) | set(agg.by_day_write))
    ]
    by_action_sorted = sorted(agg.by_action.items(), key=lambda kv: kv[1], reverse=True)
    by_action_top: list[dict[str, Any]] = [
        {"action": k, "count": v} for k, v in by_action_sorted[:15]
    ]
    all_agents = sorted(
        set(agg.by_agent_search) | set(agg.by_agent_write),
        key=lambda a: -(agg.by_agent_search.get(a, 0) + agg.by_agent_write.get(a, 0)),
    )
    by_agent = [
        AgentRow(
            agent_id=a,
            search=agg.by_agent_search.get(a, 0),
            write=agg.by_agent_write.get(a, 0),
            ratio=round(agg.by_agent_search.get(a, 0) / max(agg.by_agent_write.get(a, 0), 1), 3),
        )
        for a in all_agents
    ]
    ratio = agg.search_total / max(agg.write_total, 1)
    total_with_hits_data = agg.search_calls_with_hits + agg.search_calls_zero_hits
    hit_rate = agg.search_calls_with_hits / max(total_with_hits_data, 1)
    return TelemetryResponse(
        workspace_id=workspace_id,
        since=since_iso,
        until=until_iso,
        search_total=agg.search_total,
        write_total=agg.write_total,
        search_per_write_ratio=round(ratio, 3),
        per_day=per_day,
        by_action_top=by_action_top,
        by_agent=by_agent,
        search_calls_with_hits=agg.search_calls_with_hits,
        search_calls_zero_hits=agg.search_calls_zero_hits,
        search_hit_rate=round(hit_rate, 3),
    )
