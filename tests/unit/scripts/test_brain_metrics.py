"""Phase 0: brain_metrics dashboard tests.

Smoke tests that exercise every phase's collector against a synthetic
schema. We do NOT assert specific counts -- only that the collector
returns the right shape and doesn't raise on empty / mixed DBs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_memory_lite.utils.time import iso_now

# Import the script module by path so we can exercise its functions.
_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "brain_metrics.py"


@pytest.fixture
def brain_metrics_module(monkeypatch: pytest.MonkeyPatch) -> object:
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("brain_metrics", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
ALL_PHASE_MIGRATIONS = [
    "0002_outcome_loop.sql",
    "0003_hebbian.sql",
    "0004_consolidation_feedback.sql",
    "0005_reflexes.sql",
    "0006_self_model.sql",
    "0007_bi_temporal.sql",
    "0008_causal_links.sql",
]


@pytest.fixture
def fully_migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "brain.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    migrations_dir = Path(__file__).resolve().parents[3] / "migrations" / "canonical"
    for name in ALL_PHASE_MIGRATIONS:
        conn.executescript((migrations_dir / name).read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return db_path


def test_dashboard_on_empty_workspace_returns_zeros(
    fully_migrated_db: Path, brain_metrics_module: object
) -> None:
    """Every phase collector must not raise on an empty DB."""
    report = brain_metrics_module.collect_workspace_report(fully_migrated_db, "ws_empty")
    assert report["workspace_id"] == "ws_empty"
    assert report["phase_1_outcome"]["decision"]["total"] == 0
    assert report["phase_2_hebbian"]["coactivation_rows"] == 0
    assert report["phase_3_consolidation"]["insights_by_status"] == {}
    assert report["phase_4_reflex"]["active_rules"] == 0
    assert report["phase_5_self_model"] == {"present": False}
    assert report["phase_6_bi_temporal"]["decision"]["total"] == 0
    assert report["phase_7_causal"]["causal_links_by_relation"] == {}


def test_dashboard_with_data_returns_counts(
    fully_migrated_db: Path, brain_metrics_module: object
) -> None:
    """Seed one row per phase, run the dashboard, check the counts come through."""
    conn = sqlite3.connect(fully_migrated_db)
    conn.row_factory = sqlite3.Row
    # Phase 1: outcome.
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES ('dec_x', 'ws', 'T', 'B', 'active', ?, ?, ?, 0.5, 0)""",
        (iso_now(), iso_now(), iso_now()),
    )
    # Phase 2: soft edge.
    conn.execute(
        """INSERT INTO soft_edges
           (id, workspace_id, src_qualified_name, dst_qualified_name,
            edge_kind, weight, observation_count, last_seen_at, created_at,
            metadata_json)
           VALUES ('s_x', 'ws', 'decision:a', 'theory:b', 'co_retrieved',
                   1.5, 1, ?, ?, '{}')""",
        (iso_now(), iso_now()),
    )
    # Phase 3: insight.
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, status, confidence,
            created_at, updated_at)
           VALUES ('ins_x', 'ws', 'consolidation', 's', 'candidate',
                   0.6, ?, ?)""",
        (iso_now(), iso_now()),
    )
    # Phase 4: reflex.
    conn.execute(
        """INSERT INTO reflex_rules
           (id, workspace_id, rule_name, trigger_tool, trigger_pattern,
            precondition_kind, precondition_param_json, enforcement,
            active, block_count, advisory_count, created_at, updated_at)
           VALUES ('r_x', 'ws', 'r1', 'Edit', '', 'impact_check_within_seconds',
                   '{}', 'advisory', 1, 0, 3, ?, ?)""",
        (iso_now(), iso_now()),
    )
    # Phase 5: self_model.
    conn.execute(
        """INSERT INTO self_model
           (workspace_id, identity_text, coverage_score, refreshed_via,
            created_at, updated_at)
           VALUES ('ws', 'I am the agent.', 0.5, 'heuristic', ?, ?)""",
        (iso_now(), iso_now()),
    )
    # Phase 6: bi-temporal (decision_x already aged_out=0 because valid_to NULL).
    conn.execute("UPDATE decisions SET valid_to = ? WHERE id = 'dec_x'", (iso_now(),))
    # Phase 7: causal link.
    conn.execute(
        """INSERT INTO causal_links
           (id, workspace_id, src_kind, src_id, dst_kind, dst_id,
            relation, weight, created_at)
           VALUES ('c_x', 'ws', 'decision', 'a', 'decision', 'b',
                   'invalidated', 1.0, ?)""",
        (iso_now(),),
    )
    conn.commit()
    conn.close()

    report = brain_metrics_module.collect_workspace_report(fully_migrated_db, "ws")
    assert report["phase_1_outcome"]["decision"]["total"] == 1
    assert report["phase_1_outcome"]["decision"]["positive"] == 1
    assert report["phase_2_hebbian"]["soft_edges_by_kind"]["co_retrieved"]["count"] == 1
    assert report["phase_3_consolidation"]["insights_by_status"]["candidate"] == 1
    assert report["phase_4_reflex"]["active_rules"] == 1
    assert report["phase_4_reflex"]["advisory_fires"] == 3
    assert report["phase_5_self_model"]["present"] is True
    assert report["phase_5_self_model"]["refreshed_via"] == "heuristic"
    assert report["phase_6_bi_temporal"]["decision"]["aged_out"] == 1
    assert report["phase_7_causal"]["causal_links_by_relation"]["invalidated"] == 1


def test_dashboard_human_render_handles_empty(
    fully_migrated_db: Path, brain_metrics_module: object
) -> None:
    report = brain_metrics_module.collect_workspace_report(fully_migrated_db, "ws_x")
    text = brain_metrics_module.render_human(report)
    assert "# Brain metrics: ws_x" in text


def test_dashboard_on_pre_migration_db_safe(tmp_path: Path, brain_metrics_module: object) -> None:
    """Apply ONLY 0001; the dashboard skips missing-table phases gracefully."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    report = brain_metrics_module.collect_workspace_report(db_path, "ws_pre")
    assert report["workspace_id"] == "ws_pre"
    # New tables / columns return empty, not error.
    assert report["phase_4_reflex"]["active_rules"] == 0
    assert report["phase_5_self_model"] == {"present": False}
    assert report["phase_7_causal"]["causal_links_by_relation"] == {}


def test_collector_handles_missing_db(brain_metrics_module: object) -> None:
    report = brain_metrics_module.collect_workspace_report(Path("/nonexistent.db"), "ws")
    assert report.get("error") == "db_missing"
