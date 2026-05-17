"""Unit tests for the search-before-architectural-write detector."""

from __future__ import annotations

from agent_memory_lite.enforcement.mechanical_search_before_arch import (
    detect_arch_write_without_search,
)


def test_write_decision_without_search_blocks() -> None:
    out = detect_arch_write_without_search("memory_write_decision", {"title": "New decision"}, [])
    assert out is not None
    assert "memory_write_decision" in out


def test_write_decision_after_search_passes() -> None:
    assert (
        detect_arch_write_without_search(
            "memory_write_decision",
            {"title": "T"},
            ["memory_search"],
        )
        is None
    )


def test_write_decision_after_list_decisions_passes() -> None:
    assert (
        detect_arch_write_without_search(
            "memory_write_decision",
            {"title": "T"},
            ["memory_list_decisions"],
        )
        is None
    )


def test_write_theory_treated_same() -> None:
    out = detect_arch_write_without_search("memory_write_theory", {"title": "Hyp"}, [])
    assert out is not None


def test_record_with_evidence_treated_same() -> None:
    out = detect_arch_write_without_search("memory_record_with_evidence", {"title": "T"}, [])
    assert out is not None


def test_mcp_prefixed_tool_caught() -> None:
    out = detect_arch_write_without_search(
        "mcp__agent-memory-lite__memory_write_decision",
        {"title": "T"},
        [],
    )
    assert out is not None


def test_get_context_counts_as_prior_search() -> None:
    """memory_get_context exposes the same prior-art surface."""
    assert (
        detect_arch_write_without_search(
            "memory_write_decision",
            {"title": "T"},
            ["memory_get_context"],
        )
        is None
    )


def test_non_arch_write_tool_passes() -> None:
    for name in ("memory_ingest_episode", "Edit", "Write", "Read"):
        assert detect_arch_write_without_search(name, {"title": "T"}, []) is None


def test_unrelated_prior_tools_do_not_count() -> None:
    out = detect_arch_write_without_search(
        "memory_write_decision", {"title": "T"}, ["Read", "Bash"]
    )
    assert out is not None


def test_record_with_evidence_uses_decision_title_in_diagnostic() -> None:
    """memory_record_with_evidence renames title to decision_title — surface it."""
    out = detect_arch_write_without_search(
        "memory_record_with_evidence", {"decision_title": "Live arm gate"}, []
    )
    assert out is not None
    assert "Live arm gate" in out
