"""POST /memory/compact."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.schemas.compact import CompactRequest, CompactResponse
from agent_memory_lite.compaction.invalidate_stale import archive_stale_facts
from agent_memory_lite.compaction.summarize_old import summarize_old_episodes

router = APIRouter()


@router.post("/memory/compact", response_model=CompactResponse)
def compact_route(body: CompactRequest, conn: DbDep, settings: SettingsDep) -> CompactResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    # 1.2.5: pass settings so reflective compaction (v1.8) actually
    # runs. Pre-1.2.5 settings was None at this call site, which
    # silently disabled lesson_candidate emission even when the
    # MEMORY_REFLECTIVE_COMPACT_ENABLED flag was on. Diagnosed in
    # copyBot 2026-05-09: 332 episodes, 0 insight_candidates ever.
    # Body's summarize_age_days takes priority; falls back to the
    # workspace default settings.compact_age_days.
    age_days = body.summarize_age_days or settings.compact_age_days
    summary = summarize_old_episodes(
        conn,
        workspace_id=body.workspace_id,
        age_days=age_days,
        settings=settings,
    )
    stale = archive_stale_facts(
        conn,
        workspace_id=body.workspace_id,
        max_age_days=body.stale_age_days,
    )
    return CompactResponse(
        summarized_episodes=summary.summarized_episodes,
        summary_episode_id=summary.summary_episode_id,
        lesson_candidates_emitted=summary.lesson_candidates_emitted,
        stale_facts_archived=stale.stale_count,
        cutoff_for_stale=stale.cutoff,
    )
