"""GET /memory/sentinel_trends — per-case pass/fail rates over a window (v1.9).

Returns aggregated retrieval_sentinel_results so the operator can spot
regressions over time rather than just the latest pass/fail snapshot.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.maintenance.sentinel_persistence import sentinel_trends

router = APIRouter()


class SentinelTrendCaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_name: str
    runs: int
    passes: int
    failures: int
    pass_rate: float
    last_status: str
    last_seen_at: str


class SentinelTrendsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    window_days: int
    cases: list[SentinelTrendCaseResponse]


@router.get("/memory/sentinel_trends", response_model=SentinelTrendsResponse)
def sentinel_trends_route(
    settings: SettingsDep,
    conn: DbDep,
    workspace_id: str = Query(default="default"),
    window_days: int = Query(default=7, ge=1, le=365),
) -> SentinelTrendsResponse:
    ensure_workspace_readable(workspace_id, settings)
    trends = sentinel_trends(conn, workspace_id=workspace_id, window_days=window_days)
    return SentinelTrendsResponse(
        workspace_id=workspace_id,
        window_days=window_days,
        cases=[
            SentinelTrendCaseResponse(
                case_name=t.case_name,
                runs=t.runs,
                passes=t.passes,
                failures=t.failures,
                pass_rate=(t.passes / t.runs) if t.runs else 0.0,
                last_status=t.last_status,
                last_seen_at=t.last_seen_at,
            )
            for t in trends
        ],
    )
