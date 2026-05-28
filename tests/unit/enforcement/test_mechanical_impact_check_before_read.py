"""Unit tests for the impact-check-before-read trail-aware detector."""

from __future__ import annotations

from agent_memory_lite.enforcement.mechanical_impact_check_before_read import (
    detect_read_without_impact_check,
)


def test_read_without_prior_impact_check_blocks() -> None:
    out = detect_read_without_impact_check("Read", {"file_path": "src/a.py"}, [])
    assert out is not None
    assert "Read" in out
    assert "src/a.py" in out
    assert "memory_impact_check" in out


def test_grep_without_prior_impact_check_blocks() -> None:
    out = detect_read_without_impact_check("Grep", {"pattern": "foo", "path": "src/"}, [])
    assert out is not None
    assert "Grep" in out
    assert "memory_impact_check" in out


def test_read_after_memory_impact_check_passes() -> None:
    out = detect_read_without_impact_check(
        "Read",
        {"file_path": "src/a.py"},
        ["mcp__agent-memory-lite__memory_impact_check"],
    )
    assert out is None


def test_grep_after_memory_impact_check_passes() -> None:
    out = detect_read_without_impact_check(
        "Grep",
        {"pattern": "foo"},
        ["mcp__agent-memory-lite__memory_impact_check"],
    )
    assert out is None


def test_prior_read_alone_does_not_authorize_read() -> None:
    """Only memory_impact_check counts — a prior Read does not satisfy the rule."""
    out = detect_read_without_impact_check(
        "Read",
        {"file_path": "src/a.py"},
        ["Read"],
    )
    assert out is not None


def test_non_target_tool_passes() -> None:
    """Edit / Write / NotebookEdit / MultiEdit / Bash are handled by other rules."""
    for name in ("Edit", "Write", "NotebookEdit", "MultiEdit", "Bash"):
        assert detect_read_without_impact_check(name, {"file_path": "x"}, []) is None


def test_glob_without_prior_impact_check_blocks() -> None:
    out = detect_read_without_impact_check("Glob", {"pattern": "**/*.py"}, [])
    assert out is not None
    assert "Glob" in out
    assert "memory_impact_check" in out


def test_notebook_read_without_prior_impact_check_blocks() -> None:
    out = detect_read_without_impact_check("NotebookRead", {"notebook_path": "nb.ipynb"}, [])
    assert out is not None
    assert "NotebookRead" in out
    assert "nb.ipynb" in out


def test_missing_target_reports_unknown() -> None:
    out = detect_read_without_impact_check("Read", {}, [])
    assert out is not None
    assert "(unknown)" in out


def test_grep_falls_back_to_pattern_when_no_path() -> None:
    """Grep without a path falls back to the pattern in the diagnostic."""
    out = detect_read_without_impact_check("Grep", {"pattern": "my_symbol"}, [])
    assert out is not None
    assert "my_symbol" in out


def test_unrelated_prior_tools_do_not_count() -> None:
    """Bash or Edit history without an impact check does NOT authorize the Read."""
    out = detect_read_without_impact_check(
        "Read", {"file_path": "src/a.py"}, ["Bash", "Edit", "Grep"]
    )
    assert out is not None


def test_non_dict_input_passes() -> None:
    """Malformed payload short-circuits to None (no diagnostic)."""
    out = detect_read_without_impact_check("Read", None, [])  # type: ignore[arg-type]
    assert out is None
