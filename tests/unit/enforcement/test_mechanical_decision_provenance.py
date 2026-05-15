"""Unit tests for the decision-provenance mechanical detector."""

from __future__ import annotations

from agent_memory_lite.enforcement.mechanical_decision_provenance import (
    detect_decision_without_provenance,
)


def test_non_decision_tool_passes() -> None:
    assert detect_decision_without_provenance("memory_ingest_episode", {"raw_text": "..."}) is None


def test_decision_with_source_episode_id_passes() -> None:
    assert (
        detect_decision_without_provenance(
            "memory_write_decision",
            {
                "title": "Switch to X",
                "source_episode_id": "ep_123",
                "decision_text": "...",
            },
        )
        is None
    )


def test_decision_with_long_rationale_passes() -> None:
    assert (
        detect_decision_without_provenance(
            "memory_write_decision",
            {
                "title": "Use Y",
                "rationale": "A B C D E F G H I J K L M N O P",
            },
        )
        is None
    )


def test_decision_with_short_rationale_blocks() -> None:
    out = detect_decision_without_provenance(
        "memory_write_decision",
        {"title": "Use Z", "rationale": "ok"},
    )
    assert out is not None
    assert "provenance" in out


def test_decision_with_allow_orphan_passes() -> None:
    assert (
        detect_decision_without_provenance(
            "memory_write_decision",
            {"title": "Predates recording", "allow_orphan": True},
        )
        is None
    )


def test_decision_with_no_provenance_at_all_blocks() -> None:
    out = detect_decision_without_provenance("memory_write_decision", {"title": "Bare decision"})
    assert out is not None
    assert "Bare decision" in out


def test_mcp_prefixed_tool_name_also_caught() -> None:
    out = detect_decision_without_provenance(
        "mcp__agent-memory-lite__memory_write_decision", {"title": "T"}
    )
    assert out is not None


def test_record_with_evidence_also_caught() -> None:
    """memory_record_with_evidence is a write_decision in disguise."""
    out = detect_decision_without_provenance(
        "memory_record_with_evidence",
        {"title": "Bundled write", "episode_raw_text": "..."},
    )
    assert out is not None


def test_record_with_evidence_with_provenance_passes() -> None:
    """When episode_raw_text+source establishes provenance via long rationale."""
    out = detect_decision_without_provenance(
        "memory_record_with_evidence",
        {
            "title": "Bundled",
            "rationale": "Evidence captured in same call via episode_raw_text payload",
        },
    )
    assert out is None


def test_non_dict_payload_passes_silently() -> None:
    """Malformed input should not crash the detector."""
    assert detect_decision_without_provenance("memory_write_decision", None) is None  # type: ignore[arg-type]
