from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.maintenance.retrieval_quality import (
    RetrievalQualityCase,
    run_retrieval_quality_evals,
)
from agent_memory_lite.maintenance.sentinel_persistence import (
    SentinelResultIn,
    record_sentinel_run,
    sentinel_trends,
)
from agent_memory_lite.maintenance.sentinel_scheduler import _to_sentinel_status
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn


def test_retrieval_quality_detects_expected_chunk_and_sources(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="project-a",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="heap_watchdog v2 fixed state refresh memory pressure",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    report = run_retrieval_quality_evals(
        applied_conn,
        workspace_id="project-a",
        cases=[
            RetrievalQualityCase(
                name="heap_watchdog",
                query="heap_watchdog memory pressure",
                expected_ids=[result.chunk.id],
                expected_context_ids=[result.chunk.id],
                expected_sources=["fts"],
                top_k=3,
            )
        ],
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    assert report.status == "ok"
    assert report.cases_passed == 1
    assert report.recall_at_k == 1.0
    assert report.mrr == 1.0
    assert report.ndcg_at_k == 1.0
    assert report.context_hit_rate == 1.0
    assert report.results[0].matched_context_ids == [result.chunk.id]


def test_retrieval_quality_degrades_on_missing_expected_id(
    applied_conn: sqlite3.Connection,
) -> None:
    report = run_retrieval_quality_evals(
        applied_conn,
        workspace_id="project-a",
        cases=[
            RetrievalQualityCase(
                name="missing",
                query="no such query",
                expected_ids=["chk_missing"],
                top_k=3,
            )
        ],
    )

    assert report.status == "degraded"
    assert report.failures


def test_brief_budget_surfaces_relevant_decision_under_compact_surface(
    applied_conn: sqlite3.Connection,
) -> None:
    write_canonical(
        applied_conn,
        workspace_id="project-a",
        kind="decision",
        payload={
            "title": "Shadow vs real-paper edge delta on paired-cohort",
            "decision_text": "Track paired cohort delta before changing selector policy.",
            "rationale": "The compact brief must surface current decisions under budget.",
            "importance": 0.9,
        },
    )
    write_canonical(
        applied_conn,
        workspace_id="project-a",
        kind="behavior",
        payload={
            "name": "Very verbose research operator",
            "kind": "operating_rule",
            "scope": "workspace",
            "priority": "user_preference",
            "rule": " ".join(["long behavior text"] * 140),
            "confidence": 0.95,
        },
    )

    report = run_retrieval_quality_evals(
        applied_conn,
        workspace_id="project-a",
        cases=[
            RetrievalQualityCase(
                name="decision_budget",
                query="shadow versus real paper edge delta paired cohort",
                expected_sections=["decisions"],
                expected_object_titles=[
                    "Shadow vs real-paper edge delta on paired-cohort",
                ],
                min_render_level="summary",
                expected_omissions_absent=True,
                max_tokens=2500,
            )
        ],
    )

    assert report.status == "ok"
    assert report.results[0].render_levels["decisions"] == "summary"


def test_sentinel_status_translates_to_trend_vocabulary(
    applied_conn: sqlite3.Connection,
) -> None:
    """Regression: the runner + RetrievalQualityReport emit 'passed'/'failed', but
    record_sentinel_run's audit counts, the retrieval_sentinel_results rows, and
    the sentinel_trends query all match 'pass'/'fail'. The scheduler must
    translate at the boundary -- otherwise /memory/sentinel_trends silently
    reports 0 passes / 0 failures forever."""
    assert _to_sentinel_status("passed") == "pass"
    assert _to_sentinel_status("failed") == "fail"
    # End-to-end: a translated result is actually counted by the trend query.
    record_sentinel_run(
        applied_conn,
        workspace_id="project-a",
        results=[
            SentinelResultIn(case_name="c_ok", status=_to_sentinel_status("passed")),
            SentinelResultIn(case_name="c_bad", status=_to_sentinel_status("failed")),
        ],
    )
    trends = {
        t.case_name: t
        for t in sentinel_trends(applied_conn, workspace_id="project-a", window_days=30)
    }
    assert trends["c_ok"].passes == 1
    assert trends["c_ok"].failures == 0
    assert trends["c_bad"].passes == 0
    assert trends["c_bad"].failures == 1
    # The raw runner vocabulary is NOT counted -- this is exactly the bug the
    # translation fixes (a 'passed' row matches neither 'pass' nor 'fail').
    record_sentinel_run(
        applied_conn,
        workspace_id="project-a",
        results=[SentinelResultIn(case_name="c_raw", status="passed")],
    )
    raw = {
        t.case_name: t
        for t in sentinel_trends(applied_conn, workspace_id="project-a", window_days=30)
    }
    assert raw["c_raw"].runs == 1
    assert raw["c_raw"].passes == 0
    assert raw["c_raw"].failures == 0


def test_sentinel_pass_uses_row_connection_no_typeerror(
    tmp_path, fake_embedding_provider, fake_vector_store
) -> None:
    """Regression: _run_sentinel_pass opened a tuple-row connection (no
    row_factory=Row), so run_case raised 'TypeError: tuple indices must be
    integers or slices, not str' on the first row["col"] access OVER A RETRIEVED
    ROW -- every run crashed. On the live DB this recorded 19/19 'failed' that
    were really this crash. We must seed retrievable content (an ingested chunk)
    so run_case actually processes a row; with a Row connection it grades
    normally and does NOT crash. The existing evals tests never caught this
    because they pass a Row `applied_conn` directly."""
    import sqlite3 as _sqlite3  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from agent_memory_lite.config.settings import Settings  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415
    from agent_memory_lite.maintenance.sentinel_scheduler import _run_sentinel_pass  # noqa: PLC0415

    db = tmp_path / "m.db"
    conn = _sqlite3.connect(db)
    conn.row_factory = _sqlite3.Row
    apply_migrations(conn)
    # Seed a retrievable chunk so the FTS query returns a row that run_case then
    # accesses by string key -- the only path that triggers the tuple crash.
    ingest_episode(
        conn,
        EpisodeIn(
            workspace_id="ws",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="never run git push without explicit operator permission",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    conn.commit()
    conn.close()
    (tmp_path / "retrieval_sentinels.yaml").write_text(
        "- name: gp\n  query: git push permission\n"
        "  expected_substrings:\n    - git push\n  top_k: 5\n",
        encoding="utf-8",
    )
    settings = Settings().model_copy(update={"brain_pass_enabled": False, "db_path": str(db)})
    _run_sentinel_pass(
        workspace_id="ws",
        db_path=Path(db),
        settings=settings,
        embedding_provider=None,
        vector_store=None,
    )
    rconn = _sqlite3.connect(db)
    rconn.row_factory = _sqlite3.Row
    rows = rconn.execute(
        "SELECT status, failures_json FROM retrieval_sentinel_results WHERE workspace_id = 'ws'"
    ).fetchall()
    rconn.close()
    assert rows, "the sentinel pass recorded no result (workspace guard aborted?)"
    blob = " ".join((r["failures_json"] or "") for r in rows)
    assert "TypeError" not in blob, blob
    assert "tuple indices" not in blob, blob


def test_retrieval_quality_checks_object_titles_and_render_level(
    applied_conn: sqlite3.Connection,
) -> None:
    write_canonical(
        applied_conn,
        workspace_id="project-a",
        kind="decision",
        payload={
            "title": "Shadow vs real-paper edge delta on paired-cohort",
            "decision_text": "Paired cohort delta is positive enough to keep tracking.",
            "importance": 0.9,
        },
    )

    report = run_retrieval_quality_evals(
        applied_conn,
        workspace_id="project-a",
        cases=[
            RetrievalQualityCase(
                name="research_budget_stub",
                query="shadow versus real paper edge delta paired cohort experiment",
                expected_sections=["decisions"],
                expected_object_titles=[
                    "Shadow vs real-paper edge delta on paired-cohort",
                ],
                min_render_level="stub",
                expected_omissions_absent=True,
                max_tokens=2500,
            )
        ],
    )

    assert report.status == "ok"
    assert report.results[0].render_levels["decisions"] == "summary"
