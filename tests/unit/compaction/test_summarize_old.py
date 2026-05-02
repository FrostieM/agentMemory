from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.compaction.summarize_old import summarize_old_episodes
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.repositories.episodes_repo import insert_episode
from agent_memory_lite.utils.time import reset_now_provider, set_now_provider


def test_no_old_episodes_returns_empty_stats(applied_conn: sqlite3.Connection) -> None:
    insert_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="recent",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
    )
    stats = summarize_old_episodes(applied_conn, workspace_id="default", age_days=30)
    assert stats.summarized_episodes == 0
    assert stats.summary_episode_id is None


def test_old_episodes_summarized(applied_conn: sqlite3.Connection) -> None:
    fixed_old = datetime(2025, 1, 1, tzinfo=UTC)
    set_now_provider(lambda: fixed_old)
    try:
        insert_episode(
            applied_conn,
            EpisodeIn(
                workspace_id="default",
                source_type=EpisodeSource.AGENT_ACTION,
                raw_text="ancient event",
                trust_level=TrustLevel.AGENT_OBSERVED,
            ),
        )
    finally:
        reset_now_provider()
    set_now_provider(lambda: fixed_old + timedelta(days=120))
    try:
        stats = summarize_old_episodes(applied_conn, workspace_id="default", age_days=30)
    finally:
        reset_now_provider()
    assert stats.summarized_episodes == 1
    assert stats.summary_episode_id is not None
