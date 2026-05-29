"""Unit tests for v3 compact projections."""

from __future__ import annotations

import json
import sqlite3

import pytest

from agent_memory_lite.storage.projections import (
    known_kinds,
    project,
    project_behavior,
    project_chunk,
    project_code_digest,
    project_concept,
    project_decision,
    project_episode,
    project_insight,
    project_plan_step,
    project_skill,
    project_snapshot,
    project_task,
    project_theory,
)


def _row(**kwargs) -> sqlite3.Row:
    """Build a sqlite3.Row from kwargs (using an in-memory cursor)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" * len(kwargs))
    conn.execute(f"CREATE TABLE t ({cols})")
    conn.execute(f"INSERT INTO t ({cols}) VALUES ({placeholders})", tuple(kwargs.values()))
    row = conn.execute("SELECT * FROM t").fetchone()
    conn.close()
    return row


def test_known_kinds_returns_thirteen_kinds() -> None:
    kinds = known_kinds()
    assert len(kinds) == 13
    for expected in (
        "decision",
        "theory",
        "behavior",
        "skill",
        "episode",
        "concept",
        "task",
        "insight",
        "issue",
        "snapshot",
        "code_digest",
        "chunk",
        "plan_step",
    ):
        assert expected in kinds


def test_project_decision_compact_shape() -> None:
    row = _row(
        id="dec_x",
        title="Adopt canonical",
        decision_text="full body",
        rationale="full rationale",
        gist="Adopt v3 for speed",
        status="active",
        supersedes_decision_id=None,
        pinned=1,
        valid_from="2026-05-17T00:00:00Z",
    )
    out = project_decision(row)
    assert out["id"] == "dec_x"
    assert out["kind"] == "decision"
    assert out["title"] == "Adopt canonical"
    assert out["gist"] == "Adopt v3 for speed"
    assert out["pinned"] is True
    assert "decision_text" not in out
    assert "rationale" not in out


def test_project_plan_step_compact_shape() -> None:
    row = _row(
        id="pstep_x",
        task_id="t1",
        title="Write the migration",
        body="full step body",
        status="active",
        rank=2.0,
        parent_step_id=None,
    )
    out = project_plan_step(row)
    assert out["id"] == "pstep_x"
    assert out["kind"] == "plan_step"
    assert out["task_id"] == "t1"
    assert out["title"] == "Write the migration"
    assert out["status"] == "active"
    assert out["rank"] == 2.0
    assert "body" not in out


def test_project_theory_falls_back_to_claim_when_gist_missing() -> None:
    row = _row(
        id="th_x",
        title="T",
        domain="trading",
        claim="Hypothesis text",
        gist=None,
        status="proposed",
        evidence_count=2,
        confidence=0.4,
    )
    out = project_theory(row)
    assert out["id"] == "th_x"
    assert out["gist"] == "Hypothesis text"
    assert out["evidence_count"] == 2


def test_project_behavior_includes_applies_to_csv() -> None:

    row = _row(
        id="beh_x",
        name="no-git-push",
        kind="operating_rule",
        rule="full rule body",
        rule_one_line="Never push without approval",
        applies_to_json=json.dumps(["git push", "git commit", "ship to main", "deploy"]),
        pinned=1,
    )
    out = project_behavior(row)
    assert out["name"] == "no-git-push"
    assert "git push" in out["applies_to_csv"]
    assert out["pinned"] is True
    assert "rule" not in out  # full rule body NOT in projection


def test_project_skill_omits_body_md() -> None:
    row = _row(
        id="skill_x",
        subtype="skill",
        name="kelly-sizing",
        summary="Compute Kelly fraction",
        when_to_use_short="Before sizing a bet",
        body_md="LONG MARKDOWN BODY HERE",
        body_token_count=120,
        usage_count=3,
    )
    out = project_skill(row)
    assert out["id"] == "skill_x"
    assert out["subtype"] == "skill"
    assert out["body_token_count"] == 120
    assert "body_md" not in out  # body NEVER returned in projection


def test_project_episode_omits_raw_text() -> None:
    row = _row(
        id="ep_x",
        created_at="2026-05-17T00:00:00Z",
        source_type="agent_action",
        raw_text="LONG RAW TEXT HERE",
        gist="Brief summary",
        task_id="task_1",
    )
    out = project_episode(row)
    assert out["id"] == "ep_x"
    assert out["gist"] == "Brief summary"
    assert "raw_text" not in out


def test_project_concept_with_aliases() -> None:

    row = _row(
        id="concept_x",
        name="kelly-fraction",
        kind="metric",
        definition="Full definition",
        definition_one_line="Bet sizing ratio",
        aliases_json=json.dumps(["k-frac", "kelly%"]),
    )
    out = project_concept(row)
    assert out["name"] == "kelly-fraction"
    assert "k-frac" in out["aliases_csv"]
    assert "kelly%" in out["aliases_csv"]
    assert "definition" not in out


def test_project_task_counts_blockers() -> None:

    row = _row(
        id="task_x",
        task_id="t1",
        goal="Full goal",
        goal_one_line="Compact goal",
        status="in_progress",
        next_action="Step 5",
        blockers_json=json.dumps(["b1", "b2", "b3"]),
    )
    out = project_task(row)
    assert out["blockers_count"] == 3
    assert out["next_action"] == "Step 5"
    assert "goal" not in out


def test_project_insight_falls_back_to_summary() -> None:
    row = _row(
        id="insight_x",
        insight_type="lesson",
        status="new",
        gist=None,
        summary="A reusable lesson observed in copyBot session",
        target_type="theory",
        target_id="th_x",
    )
    out = project_insight(row)
    assert out["id"] == "insight_x"
    assert "reusable lesson" in out["gist"]


def test_project_snapshot_compact_shape() -> None:
    row = _row(
        id="snap_x",
        snapshot_key="daily-2026-05-26",
        title="Daily import",
        source_label="warehouse",
        total_rows=42,
    )
    out = project_snapshot(row)
    assert out["id"] == "snap_x"
    assert out["kind"] == "snapshot"
    assert out["snapshot_key"] == "daily-2026-05-26"
    assert out["total_rows"] == 42


def test_project_code_digest_extracts_symbol_csv() -> None:

    row = _row(
        id="digest_x",
        file_path="src/strategy.py",
        language="python",
        purpose_short="Kelly + signal calibration",
        top_symbols_json=json.dumps(
            [{"name": "calibrate", "kind": "function"}, {"name": "Strategy", "kind": "class"}]
        ),
        symbol_count=2,
        inbound_edge_count=5,
        pagerank=0.04,
    )
    out = project_code_digest(row)
    assert out["file_path"] == "src/strategy.py"
    assert "calibrate" in out["top_symbols_csv"]
    assert "Strategy" in out["top_symbols_csv"]
    assert out["inbound_edge_count"] == 5


def test_project_chunk_returns_gist_not_text() -> None:
    row = _row(
        id="chunk_x",
        kind="code",
        text="LONG CODE",
        summary="brief",
        gist="One-line gist",
        qualified_name="strategy.calibrate",
        symbol_kind="function",
        line_start=42,
    )
    out = project_chunk(row)
    assert out["gist"] == "One-line gist"
    assert "text" not in out


def test_project_dispatches_by_kind() -> None:
    row = _row(id="dec_y", title="T", gist="g")
    assert project("decision", row)["id"] == "dec_y"
    assert project("unknown_kind", row) is None


def test_project_decision_with_null_gist_returns_none_gist() -> None:
    row = _row(
        id="dec_x",
        title="T",
        decision_text="...",
        rationale="...",
        gist=None,
        status="active",
        supersedes_decision_id=None,
        pinned=0,
        valid_from="2026-05-17T00:00:00Z",
    )
    out = project_decision(row)
    assert out["gist"] is None


@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        ("decision", ["id", "kind", "title", "gist"]),
        ("behavior", ["id", "kind", "name", "rule_one_line"]),
        ("skill", ["id", "kind", "subtype", "name"]),
        ("episode", ["id", "kind", "ts", "source_type"]),
    ],
)
def test_every_kind_has_id_and_kind(kind: str, fields: list[str]) -> None:
    """Common contract: id + kind always present in projections."""
    if kind == "decision":
        row = _row(id="x", title="T", status="active", valid_from="t")
    elif kind == "behavior":
        row = _row(id="x", name="n", kind="operating_rule", applies_to_json="[]")
    elif kind == "skill":
        row = _row(id="x", subtype="skill", name="n")
    else:
        row = _row(id="x", created_at="t", source_type="manual")
    out = project(kind, row)
    assert out is not None
    for f in fields:
        assert f in out, f"projection of {kind} missing required field {f}"
