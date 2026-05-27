"""Unit tests for the impact-before-edit trail-aware detector."""

from __future__ import annotations

from agent_memory_lite.enforcement.mechanical_read_before_edit import (
    detect_edit_without_read,
)


def test_edit_without_prior_impact_check_blocks() -> None:
    out = detect_edit_without_read("Edit", {"file_path": "src/a.py", "new_string": "x"}, [])
    assert out is not None
    assert "Edit" in out
    assert "src/a.py" in out
    assert "memory_impact_check" in out


def test_edit_after_memory_impact_check_passes() -> None:
    out = detect_edit_without_read(
        "Edit",
        {"file_path": "src/a.py", "new_string": "x"},
        ["mcp__agent-memory-lite__memory_impact_check"],
    )
    assert out is None


def test_read_alone_no_longer_authorizes_edit() -> None:
    out = detect_edit_without_read(
        "Edit",
        {"file_path": "src/a.py", "new_string": "x"},
        ["Read"],
    )
    assert out is not None


def test_write_treated_same_as_edit() -> None:
    out = detect_edit_without_read("Write", {"file_path": "src/a.py", "content": "x"}, [])
    assert out is not None
    assert "Write" in out


def test_notebook_edit_treated_same_as_edit() -> None:
    out = detect_edit_without_read(
        "NotebookEdit",
        {"file_path": "nb.ipynb", "cell_id": "1", "new_source": "x"},
        [],
    )
    assert out is not None


def test_multi_edit_treated_same_as_edit() -> None:
    out = detect_edit_without_read("MultiEdit", {"file_path": "src/a.py", "edits": []}, [])
    assert out is not None
    assert "MultiEdit" in out


def test_non_edit_tool_passes() -> None:
    """Read/Grep/Glob never block this rule."""
    for name in ("Read", "Grep", "Glob", "Bash"):
        assert detect_edit_without_read(name, {}, []) is None


def test_missing_file_path_reports_unknown() -> None:
    out = detect_edit_without_read("Edit", {"new_string": "x"}, [])
    assert out is not None
    assert "(unknown)" in out


def test_unrelated_prior_tools_do_not_count() -> None:
    """Bash or Grep without an impact check does NOT authorize the Edit."""
    out = detect_edit_without_read(
        "Edit", {"file_path": "src/a.py", "new_string": "x"}, ["Bash", "Grep"]
    )
    assert out is not None
