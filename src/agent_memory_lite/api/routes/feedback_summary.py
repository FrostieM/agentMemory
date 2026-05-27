"""GET /memory/feedback_summary — read-only feedback signal rollup.

Returns a workspace-wide signal summary plus per-source aggregate rows from
the feedback signal view. Optionally triggers a fresh EWMA recompute when
``recompute=true`` and ``MEMORY_FEEDBACK_EWMA_ENABLED`` is on.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.maintenance.feedback_signal import (
    feedback_per_source_summary,
    feedback_signal_summary,
)
from agent_memory_lite.retrieval.feedback_aggregator import (
    SUPPORTED_SOURCE_TYPES,
    recompute_workspace_ewma,
)

router = APIRouter()


class FeedbackSignalEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    total_rows: int
    unique_sources: int
    self_loop_ratio: float
    last_feedback_at: str | None


class FeedbackSourceRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str
    source_id: str
    feedback_count: int
    usefulness_avg: float
    usefulness_sum: float
    last_feedback_at: str
    first_feedback_at: str


class FeedbackSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    signal: FeedbackSignalEnvelope
    rows: list[FeedbackSourceRow]
    recomputed_source_types: list[str]


@router.get("/memory/feedback_summary", response_model=FeedbackSummaryResponse)
def feedback_summary_route(
    settings: SettingsDep,
    conn: DbDep,
    workspace_id: str = Query(...),
    source_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    recompute: bool = Query(default=False),
) -> FeedbackSummaryResponse:
    recomputed: list[str] = []
    if recompute:
        # Recompute mutates feedback_ewma columns + writes audit rows. Treat
        # as a write so cross-workspace strict isolation is honoured.
        ensure_workspace_writable(workspace_id, settings)
        if settings.feedback_ewma_enabled:
            targets = (
                [source_type]
                if source_type in SUPPORTED_SOURCE_TYPES
                else list(SUPPORTED_SOURCE_TYPES)
            )
            for st in targets:
                recompute_workspace_ewma(
                    conn,
                    workspace_id=workspace_id,
                    source_type=st,
                    half_life_days=settings.feedback_halflife_days,
                    exclude_self_loop=settings.feedback_exclude_self_loop,
                    max_per_day_per_source=settings.feedback_max_per_day_per_source,
                )
                recomputed.append(st)
            conn.commit()
    signal = feedback_signal_summary(
        conn, workspace_id=workspace_id, enabled=settings.feedback_ewma_enabled
    )
    rows = feedback_per_source_summary(
        conn, workspace_id=workspace_id, source_type=source_type, limit=limit
    )
    return FeedbackSummaryResponse(
        workspace_id=workspace_id,
        signal=FeedbackSignalEnvelope(**signal.to_dict()),
        rows=[FeedbackSourceRow(**r.to_dict()) for r in rows],
        recomputed_source_types=recomputed,
    )
