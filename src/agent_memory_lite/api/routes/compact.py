"""POST /memory/compact."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_allowed
from agent_memory_lite.api.schemas.compact import CompactRequest, CompactResponse
from agent_memory_lite.compaction.invalidate_stale import archive_stale_facts
from agent_memory_lite.compaction.summarize_old import summarize_old_episodes

router = APIRouter()


@router.post("/memory/compact", response_model=CompactResponse)
def compact_route(body: CompactRequest, conn: DbDep, settings: SettingsDep) -> CompactResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    summary = summarize_old_episodes(
        conn,
        workspace_id=body.workspace_id,
        age_days=body.summarize_age_days,
    )
    stale = archive_stale_facts(
        conn,
        workspace_id=body.workspace_id,
        max_age_days=body.stale_age_days,
    )
    return CompactResponse(
        summarized_episodes=summary.summarized_episodes,
        summary_episode_id=summary.summary_episode_id,
        stale_facts_archived=stale.stale_count,
        cutoff_for_stale=stale.cutoff,
    )
