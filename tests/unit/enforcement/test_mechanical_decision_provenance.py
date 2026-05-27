"""Unit tests for the decision-provenance mechanical detector."""

from __future__ import annotations

from agent_memory_lite.enforcement.mechanical_decision_provenance import (
    detect_decision_without_provenance,
)


def _decision(**payload: object) -> dict[str, object]:
    return {"kind": "decision", "payload": payload}


def test_non_decision_tool_passes() -> None:
    assert detect_decision_without_provenance("memory_write", {"kind": "episode"}) is None


def test_decision_with_top_level_source_episode_id_passes() -> None:
    assert (
        detect_decision_without_provenance(
            "memory_write",
            {
                "kind": "decision",
                "source_episode_id": "ep_123",
                "payload": {"title": "Switch to X", "decision_text": "..."},
            },
        )
        is None
    )


def test_decision_with_payload_source_episode_id_passes() -> None:
    assert (
        detect_decision_without_provenance(
            "memory_write",
            _decision(title="Switch to X", source_episode_id="ep_123"),
        )
        is None
    )


def test_decision_with_long_rationale_passes() -> None:
    assert (
        detect_decision_without_provenance(
            "memory_write",
            _decision(
                title="Use Y",
                rationale="A B C D E F G H I J K L M N O P",
            ),
        )
        is None
    )


def test_decision_with_short_rationale_blocks() -> None:
    out = detect_decision_without_provenance(
        "memory_write",
        _decision(title="Use Z", rationale="ok"),
    )
    assert out is not None
    assert "provenance" in out


def test_decision_with_allow_orphan_passes() -> None:
    assert (
        detect_decision_without_provenance(
            "memory_write",
            _decision(title="Predates recording", allow_orphan=True),
        )
        is None
    )


def test_decision_with_no_provenance_at_all_blocks() -> None:
    out = detect_decision_without_provenance("memory_write", _decision(title="Bare decision"))
    assert out is not None
    assert "Bare decision" in out


def test_mcp_prefixed_tool_name_also_caught() -> None:
    out = detect_decision_without_provenance(
        "mcp__agent-memory-lite__memory_write", _decision(title="T")
    )
    assert out is not None


def test_decision_title_field_appears_in_diagnostic() -> None:
    out = detect_decision_without_provenance(
        "memory_write",
        _decision(decision_title="Live arm gate"),
    )
    assert out is not None
    assert "Live arm gate" in out


def test_non_dict_payload_passes_silently() -> None:
    assert detect_decision_without_provenance("memory_write", None) is None  # type: ignore[arg-type]
