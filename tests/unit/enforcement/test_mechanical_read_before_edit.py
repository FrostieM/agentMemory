"""Unit tests for the read-before-edit trail-aware detector."""

from __future__ import annotations

from agent_memory_lite.enforcement.mechanical_read_before_edit import (
    detect_edit_without_read,
)


def test_edit_without_prior_read_blocks() -> None:
    out = detect_edit_without_read("Edit", {"file_path": "src/a.py", "new_string": "x"}, [])
    assert out is not None
    assert "Edit" in out
    assert "src/a.py" in out


def test_edit_after_read_passes() -> None:
    assert (
        detect_edit_without_read("Edit", {"file_path": "src/a.py", "new_string": "x"}, ["Read"])
        is None
    )


def test_edit_after_memory_file_digest_passes() -> None:
    """memory_file_digest counts as prior read evidence."""
    out = detect_edit_without_read(
        "Edit",
        {"file_path": "src/a.py", "new_string": "x"},
        ["mcp__agent-memory-lite__memory_file_digest"],
    )
    assert out is None


def test_edit_after_memory_find_symbols_passes() -> None:
    assert (
        detect_edit_without_read(
            "Edit",
            {"file_path": "src/a.py", "new_string": "x"},
            ["memory_find_symbols"],
        )
        is None
    )


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


def test_non_edit_tool_passes() -> None:
    """Read/Grep/Glob never block this rule."""
    for name in ("Read", "Grep", "Glob", "Bash"):
        assert detect_edit_without_read(name, {}, []) is None


def test_missing_file_path_reports_unknown() -> None:
    out = detect_edit_without_read("Edit", {"new_string": "x"}, [])
    assert out is not None
    assert "(unknown)" in out


def test_unrelated_prior_tools_do_not_count() -> None:
    """Bash or Grep without a Read does NOT authorize the Edit."""
    out = detect_edit_without_read(
        "Edit", {"file_path": "src/a.py", "new_string": "x"}, ["Bash", "Grep"]
    )
    assert out is not None
