"""Wire-side schemas for `memory_compact`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CompactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    summarize_age_days: int = Field(default=30, ge=1, le=365)
    stale_age_days: int = Field(default=60, ge=1, le=730)


class CompactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summarized_episodes: int
    summary_episode_id: str | None
    # 1.2.5: number of insight_candidate rows emitted from reflective
    # compaction (v1.8). Was always implicitly 0 pre-1.2.5 because
    # settings was not passed through to summarize_old_episodes.
    lesson_candidates_emitted: int = 0
    stale_facts_archived: int
    cutoff_for_stale: str
