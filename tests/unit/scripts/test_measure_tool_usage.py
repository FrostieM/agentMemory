"""Unit tests for scripts/measure_tool_usage.py.

Covers:

* ``_iter_tool_uses`` parses tool_use blocks from JSONL transcripts
* Malformed lines / wrong shapes are skipped, not raised
* ``ProjectStats`` derived properties (graph_total, read_grep_total,
  graph_share, verdict)
* Verdict thresholds (poor / fair / good / strong)
* ``measure_project`` walks .jsonl files
* ``rollup`` aggregates across projects
* End-to-end main() with --transcript and --json
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from scripts import measure_tool_usage as m

# ============================================================
# Fixtures: write a synthetic transcript with a controlled tool mix
# ============================================================


def _write_transcript(path: Path, tool_names: list[str], session_id: str = "s1") -> None:
    """Write one .jsonl with ``tool_names`` as tool_use blocks."""
    lines = []
    for i, name in enumerate(tool_names):
        line = {
            "type": "message",
            "uuid": f"uuid_{i}",
            "sessionId": session_id,
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"tu_{i}",
                        "name": name,
                        "input": {},
                    }
                ],
            },
        }
        lines.append(json.dumps(line))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================
# _iter_tool_uses
# ============================================================


def test_iter_tool_uses_emits_each_block(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    _write_transcript(t, ["Read", "Grep", "memory_impact_check"])
    names = [tu["name"] for tu in m._iter_tool_uses(t)]
    assert names == ["Read", "Grep", "memory_impact_check"]


def test_iter_tool_uses_skips_malformed_lines(tmp_path: Path) -> None:
    t = tmp_path / "broken.jsonl"
    t.write_text(
        "not-json\n"
        + json.dumps({"type": "summary"})
        + "\n"  # no message field
        + json.dumps({"message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}})
        + "\n",
        encoding="utf-8",
    )
    names = [tu["name"] for tu in m._iter_tool_uses(t)]
    assert names == ["Read"]


def test_iter_tool_uses_handles_missing_file(tmp_path: Path) -> None:
    # No exception; just yields nothing.
    names = list(m._iter_tool_uses(tmp_path / "nope.jsonl"))
    assert names == []


# ============================================================
# ProjectStats derived properties
# ============================================================


def test_project_stats_totals_and_share() -> None:
    s = m.ProjectStats(project="x")
    s.tool_counts = Counter({"Read": 10, "Grep": 5, "memory_impact_check": 3, "Edit": 2})
    assert s.read_grep_total == 15
    assert s.graph_total == 3
    assert s.edit_total == 2
    # 3 / (3 + 15) = 0.1667 → poor verdict
    assert abs(s.graph_share - 3 / 18) < 0.001
    assert s.verdict() == "fair"  # 0.16 >= 0.15


@pytest.mark.parametrize(
    ("graph", "read_grep", "expected"),
    [
        (50, 50, "strong"),  # 0.50 hits the >= 0.50 threshold → strong
        (60, 40, "strong"),  # 0.60 → strong
        (49, 51, "good"),  # 0.49 just below strong, still ≥ 0.30 → good
        (35, 65, "good"),  # 0.35 → good
        (16, 84, "fair"),  # 0.16 → fair (≥ 0.15)
        (5, 95, "poor"),  # 0.05 → poor
        (0, 100, "poor"),
        (0, 0, "poor"),  # nothing → poor by default
    ],
)
def test_project_stats_verdict_thresholds(graph: int, read_grep: int, expected: str) -> None:
    s = m.ProjectStats(project="x")
    if graph:
        s.tool_counts["memory_impact_check"] = graph
    if read_grep:
        s.tool_counts["Read"] = read_grep
    assert s.verdict() == expected


def test_project_stats_to_dict_shape() -> None:
    s = m.ProjectStats(project="x", transcript_count=2)
    s.tool_counts = Counter({"Read": 3, "memory_impact_check": 1})
    out = s.to_dict()
    assert set(out.keys()) == {
        "project",
        "transcript_count",
        "read_grep_total",
        "graph_total",
        "edit_total",
        "graph_share",
        "verdict",
        "top_tools",
    }


# ============================================================
# measure_project
# ============================================================


def test_measure_project_walks_jsonl_files(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    _write_transcript(project / "a.jsonl", ["Read", "Read", "Grep"])
    _write_transcript(project / "b.jsonl", ["memory_impact_check"])
    stats = m.measure_project(project)
    assert stats.transcript_count == 2
    assert stats.tool_counts["Read"] == 2
    assert stats.tool_counts["Grep"] == 1
    assert stats.tool_counts["memory_impact_check"] == 1


def test_measure_project_ignores_non_jsonl(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    (project / "note.txt").write_text("not a transcript", encoding="utf-8")
    _write_transcript(project / "a.jsonl", ["Read"])
    stats = m.measure_project(project)
    assert stats.transcript_count == 1


# ============================================================
# rollup
# ============================================================


def test_rollup_aggregates_across_projects() -> None:
    a = m.ProjectStats(project="a", transcript_count=2)
    a.tool_counts = Counter({"Read": 10, "memory_impact_check": 3})
    b = m.ProjectStats(project="b", transcript_count=1)
    b.tool_counts = Counter({"Read": 5, "Grep": 2})
    total = m.rollup([a, b])
    assert total.transcript_count == 3
    assert total.tool_counts["Read"] == 15
    assert total.tool_counts["Grep"] == 2
    assert total.tool_counts["memory_impact_check"] == 3


# ============================================================
# main()
# ============================================================


def test_main_transcript_mode_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    t = tmp_path / "single.jsonl"
    _write_transcript(t, ["Read", "Read", "memory_impact_check"])
    rc = m.main(["--transcript", str(t), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["projects"][0]["read_grep_total"] == 2
    assert payload["projects"][0]["graph_total"] == 1


def test_main_transcript_missing_returns_two(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = m.main(["--transcript", str(tmp_path / "missing.jsonl"), "--json"])
    assert rc == 2


def test_main_root_mode_human(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    project = tmp_path / "p"
    project.mkdir()
    _write_transcript(project / "a.jsonl", ["memory_impact_check", "Read"])
    rc = m.main(["--root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Agent tool-usage discipline report" in out
    assert "TOTAL" in out


def test_main_project_filter(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    project = tmp_path / "p"
    project.mkdir()
    _write_transcript(project / "a.jsonl", ["Read"])
    rc = m.main(["--root", str(tmp_path), "--project", "p", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["projects"]) == 1
    assert payload["projects"][0]["project"] == "p"


def test_main_unknown_project_returns_two(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = m.main(["--root", str(tmp_path), "--project", "no_such_project"])
    assert rc == 2


def test_main_since_days_filters_by_mtime(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Old transcripts (mtime < cutoff) are excluded by --since-days."""
    import os  # noqa: PLC0415

    project = tmp_path / "p"
    project.mkdir()
    fresh = project / "fresh.jsonl"
    old = project / "old.jsonl"
    _write_transcript(fresh, ["Read"])
    _write_transcript(old, ["Read", "Grep"])
    # Backdate the "old" transcript by 30 days.
    old_mtime = old.stat().st_mtime - 30 * 86400
    os.utime(old, (old_mtime, old_mtime))
    rc = m.main(["--root", str(tmp_path), "--since-days", "7", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # Only "fresh" should be counted → 1 Read total.
    assert payload["total"]["read_grep_total"] == 1


# ============================================================
# Tool-bucket coverage assertion
# ============================================================


def test_tool_buckets_are_disjoint() -> None:
    """No tool should be in two buckets at once."""
    overlaps = (
        (m.READ_GREP_TOOLS & m.GRAPH_TOOLS)
        | (m.READ_GREP_TOOLS & m.EDIT_TOOLS)
        | (m.GRAPH_TOOLS & m.EDIT_TOOLS)
    )
    assert overlaps == set(), f"buckets overlap: {overlaps}"


def test_graph_bucket_includes_impact_check() -> None:
    """memory_impact_check must be classified as a graph tool."""
    assert "memory_impact_check" in m.GRAPH_TOOLS
