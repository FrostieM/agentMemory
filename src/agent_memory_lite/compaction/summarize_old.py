"""Summarize episodes older than a threshold.

Writes one new `EpisodeSource.SUMMARY` episode per workspace covering the
selected window and audits the action. Original episodes are left untouched
(they remain the source of truth).

v1.8: when MEMORY_REFLECTIVE_COMPACT_ENABLED is on AND a Settings instance
is passed, an additional Ollama pass extracts lesson candidates into
``insight_candidates``. The two passes are independent — the digest
summary is byte-identical to v1.3 when the reflective flag is off.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from agent_memory_lite.compaction.lesson_extractor import EpisodeWindowItem, extract_lessons
from agent_memory_lite.compaction.lesson_review import write_lesson_candidates
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.episodes_repo import insert_episode
from agent_memory_lite.utils.time import iso_now, now, parse_iso

DEFAULT_AGE_DAYS = 30
SUMMARY_PREVIEW_CHARS = 280


@dataclass(frozen=True, slots=True)
class SummarizationStats:
    summarized_episodes: int
    summary_episode_id: str | None
    lesson_candidates_emitted: int = 0


def summarize_old_episodes(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    age_days: int = DEFAULT_AGE_DAYS,
    settings: Settings | None = None,
) -> SummarizationStats:
    cutoff = (now() - timedelta(days=age_days)).isoformat()
    rows = conn.execute(
        """
        SELECT id, raw_text, summary, created_at, source_type
        FROM episodes
        WHERE workspace_id = ? AND created_at < ? AND source_type != ?
        ORDER BY created_at
        """,
        (workspace_id, cutoff, EpisodeSource.SUMMARY.value),
    ).fetchall()
    if not rows:
        return SummarizationStats(summarized_episodes=0, summary_episode_id=None)

    pieces: list[str] = []
    for row in rows:
        body = row["summary"] or row["raw_text"] or ""
        snippet = body[:SUMMARY_PREVIEW_CHARS]
        pieces.append(f"[{row['created_at']}][{row['source_type']}] {snippet}")
    digest = "\n".join(pieces[:200])  # cap memory; the raw episodes remain available

    earliest = parse_iso(rows[0]["created_at"]).isoformat()
    latest = parse_iso(rows[-1]["created_at"]).isoformat()

    with with_tx(conn):
        summary_episode = insert_episode(
            conn,
            EpisodeIn(
                workspace_id=workspace_id,
                source_type=EpisodeSource.SUMMARY,
                raw_text=digest,
                summary=f"Summary of {len(rows)} episodes from {earliest} to {latest}.",
                trust_level=TrustLevel.AGENT_OBSERVED,
                importance=0.4,
                confidence=0.8,
                metadata={
                    "covered_count": len(rows),
                    "earliest": earliest,
                    "latest": latest,
                },
            ),
        )
        insert_audit(
            conn,
            workspace_id=workspace_id,
            action="compact_summarize_old",
            target_type="episode",
            target_id=summary_episode.id,
            after={"covered": len(rows), "until": iso_now()},
        )

    lesson_candidate_count = 0
    if settings is not None and settings.reflective_compact_enabled:
        lesson_candidate_count = _emit_lesson_candidates(
            conn, workspace_id=workspace_id, rows=rows, settings=settings
        )

    return SummarizationStats(
        summarized_episodes=len(rows),
        summary_episode_id=summary_episode.id,
        lesson_candidates_emitted=lesson_candidate_count,
    )


def _emit_lesson_candidates(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    rows: list[sqlite3.Row],
    settings: Settings,
) -> int:
    window = [
        EpisodeWindowItem(
            id=str(row["id"]),
            summary=str(row["summary"] or row["raw_text"] or "")[:SUMMARY_PREVIEW_CHARS],
        )
        for row in rows
    ]
    proposals = extract_lessons(
        window,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        min_support_episodes=settings.lesson_min_support_episodes,
        max_per_run=settings.lesson_max_per_run,
    )
    candidate_ids = write_lesson_candidates(conn, workspace_id=workspace_id, proposals=proposals)
    return len(candidate_ids)
