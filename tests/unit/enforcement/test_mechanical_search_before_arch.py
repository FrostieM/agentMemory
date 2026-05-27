"""Unit tests for the search-before-architectural-write detector."""

from __future__ import annotations

from agent_memory_lite.enforcement.mechanical_search_before_arch import (
    detect_arch_write_without_search,
)


def _write(kind: str, **payload: object) -> dict[str, object]:
    return {"kind": kind, "payload": payload}


def test_write_decision_without_search_blocks() -> None:
    out = detect_arch_write_without_search(
        "memory_write", _write("decision", title="New decision"), []
    )
    assert out is not None
    assert "memory_write" in out
    assert "New decision" in out


def test_write_decision_after_search_passes() -> None:
    assert (
        detect_arch_write_without_search(
            "memory_write",
            _write("decision", title="T"),
            ["memory_search"],
        )
        is None
    )


def test_write_decision_after_non_search_memory_call_blocks() -> None:
    out = detect_arch_write_without_search(
        "memory_write",
        _write("decision", title="T"),
        ["memory_brief", "memory_get"],
    )
    assert out is not None


def test_write_theory_treated_same() -> None:
    out = detect_arch_write_without_search("memory_write", _write("theory", claim="Hypothesis"), [])
    assert out is not None
    assert "Hypothesis" in out


def test_mcp_prefixed_tool_caught() -> None:
    out = detect_arch_write_without_search(
        "mcp__agent-memory-lite__memory_write",
        _write("decision", title="T"),
        [],
    )
    assert out is not None


def test_non_arch_write_tool_passes() -> None:
    for name in ("Edit", "Write", "Read"):
        assert detect_arch_write_without_search(name, _write("decision", title="T"), []) is None
    assert (
        detect_arch_write_without_search("memory_write", _write("episode", raw_text="T"), [])
        is None
    )


def test_unrelated_prior_tools_do_not_count() -> None:
    out = detect_arch_write_without_search(
        "memory_write", _write("decision", title="T"), ["Read", "Bash"]
    )
    assert out is not None


def test_payload_title_uses_nested_decision_title() -> None:
    out = detect_arch_write_without_search(
        "memory_write", _write("decision", decision_title="Live arm gate"), []
    )
    assert out is not None
    assert "Live arm gate" in out
