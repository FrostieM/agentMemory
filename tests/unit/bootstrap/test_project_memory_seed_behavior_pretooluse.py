"""Unit tests for the PreToolUse-enforcement seed factories."""

from __future__ import annotations

from agent_memory_lite.bootstrap.project_memory_seed_behavior import (
    DISCIPLINE_FACTORIES,
    PINNED_DISCIPLINE_FACTORIES,
)
from agent_memory_lite.bootstrap.project_memory_seed_behavior_pretooluse_payload import (
    decision_must_have_provenance_pretooluse_instruction,
    no_magic_number_in_strategy_pretooluse_instruction,
)
from agent_memory_lite.bootstrap.project_memory_seed_behavior_pretooluse_trail import (
    read_before_edit_pretooluse_instruction,
    search_before_arch_write_pretooluse_instruction,
)
from agent_memory_lite.enforcement.rule_loader import MECHANICAL_TAG

PRETOOLUSE_FACTORIES = (
    no_magic_number_in_strategy_pretooluse_instruction,
    decision_must_have_provenance_pretooluse_instruction,
    read_before_edit_pretooluse_instruction,
    search_before_arch_write_pretooluse_instruction,
)

EXPECTED_DETECTOR_TAGS = {
    no_magic_number_in_strategy_pretooluse_instruction: "mechanical:no-magic-number",
    decision_must_have_provenance_pretooluse_instruction: "mechanical:decision-provenance",
    read_before_edit_pretooluse_instruction: "mechanical:read-before-edit",
    search_before_arch_write_pretooluse_instruction: "mechanical:search-before-arch",
}


def test_every_factory_returns_payload_with_required_fields() -> None:
    for factory in PRETOOLUSE_FACTORIES:
        bi = factory("test-ws", "ep_src")
        assert bi.workspace_id == "test-ws"
        assert bi.source_episode_id == "ep_src"
        assert bi.source_type == "seed_bootstrap"
        assert bi.active is True
        assert bi.confidence >= 0.9
        assert bi.rule, f"{bi.name} must have a rule body"
        assert bi.rationale, f"{bi.name} must have a rationale"


def test_every_factory_carries_enforcement_mechanical_tag() -> None:
    for factory in PRETOOLUSE_FACTORIES:
        bi = factory("ws", None)
        assert MECHANICAL_TAG in bi.applies_to, (
            f"{bi.name} missing {MECHANICAL_TAG} — PreToolUse hook would skip it"
        )


def test_every_factory_carries_its_detector_tag() -> None:
    """Each rule's applies_to must include the tag the dispatcher routes on."""
    for factory, expected_tag in EXPECTED_DETECTOR_TAGS.items():
        bi = factory("ws", None)
        assert expected_tag in bi.applies_to, f"{bi.name} missing detector tag {expected_tag!r}"


def test_factory_names_are_unique_and_prefixed_pretooluse() -> None:
    names = [factory("ws", None).name for factory in PRETOOLUSE_FACTORIES]
    assert len(names) == len(set(names)), "factory names must be unique"
    for name in names:
        assert name.startswith("pretooluse:"), (
            f"{name} should use the pretooluse:* name convention "
            f"to distinguish from foreground-reminder rules"
        )


def test_rules_include_trigger_action_invariant_sections() -> None:
    for factory in PRETOOLUSE_FACTORIES:
        rule = factory("ws", None).rule
        assert "TRIGGER" in rule, f"{factory.__name__} rule missing TRIGGER section"
        assert "ACTION" in rule, f"{factory.__name__} rule missing ACTION section"
        assert "KEY INVARIANT" in rule, f"{factory.__name__} rule missing KEY INVARIANT section"


def test_factories_registered_in_discipline_and_pinned() -> None:
    for factory in PRETOOLUSE_FACTORIES:
        assert factory in DISCIPLINE_FACTORIES, (
            f"{factory.__name__} missing from DISCIPLINE_FACTORIES"
        )
        assert factory in PINNED_DISCIPLINE_FACTORIES, (
            f"{factory.__name__} missing from PINNED_DISCIPLINE_FACTORIES"
        )


def test_payload_idempotent_under_repeated_calls() -> None:
    for factory in PRETOOLUSE_FACTORIES:
        a = factory("ws", "ep1")
        b = factory("ws", "ep1")
        assert a.name == b.name
        assert a.rule == b.rule
        assert a.applies_to == b.applies_to
