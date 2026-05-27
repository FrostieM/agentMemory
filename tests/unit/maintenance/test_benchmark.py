from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.maintenance.benchmark import run_memory_benchmarks
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn


def test_memory_benchmarks_return_operation_timings(applied_conn: sqlite3.Connection) -> None:
    ingest_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="project-a",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="benchmark sentinel token",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
    )

    report = run_memory_benchmarks(
        applied_conn,
        workspace_id="project-a",
        queries=["benchmark sentinel"],
        runs=1,
        thresholds_ms={"memory_brief": 10_000.0},
    )

    assert report.status == "ok"
    names = {result.name for result in report.results}
    assert "sqlite_quick_check" in names
    assert "integrity_audit" in names
    assert "hygiene_report" in names
    assert "quality_gate" in names
    assert "fts_search[1]" in names
    assert "memory_search[1]" in names
    assert "memory_brief[1]" in names
    assert all(result.max_ms >= 0 for result in report.results)


def test_memory_benchmarks_fail_when_threshold_is_too_low(
    applied_conn: sqlite3.Connection,
) -> None:
    report = run_memory_benchmarks(
        applied_conn,
        workspace_id="project-a",
        queries=["anything"],
        runs=1,
        thresholds_ms={"sqlite_quick_check": 0.0},
    )

    assert report.status == "degraded"
