"""Unit tests for cross-project enforcement-discipline seed factories."""

from __future__ import annotations

from agent_memory_lite.bootstrap.project_memory_seed_behavior import (
    DISCIPLINE_FACTORIES,
    PINNED_DISCIPLINE_FACTORIES,
)
from agent_memory_lite.bootstrap.project_memory_seed_behavior_post_write import (
    memory_write_resolve_candidates_instruction,
)
from agent_memory_lite.bootstrap.project_memory_seed_behavior_pre_action import (
    applies_to_checklist_verbatim_instruction,
    verification_claims_cite_evidence_instruction,
)
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)

ENFORCEMENT_FACTORIES = (
    applies_to_checklist_verbatim_instruction,
    verification_claims_cite_evidence_instruction,
    memory_write_resolve_candidates_instruction,
)


def test_each_factory_returns_active_payload_with_required_fields() -> None:
    for factory in ENFORCEMENT_FACTORIES:
        bi = factory("test-ws", "ep_src")
        assert bi.workspace_id == "test-ws"
        assert bi.source_episode_id == "ep_src"
        assert bi.source_type == "seed_bootstrap"
        assert bi.active is True
        assert bi.confidence >= 0.9
        assert bi.kind == BehaviorInstructionKind.OPERATING_RULE
        assert bi.scope == BehaviorInstructionScope.WORKSPACE
        assert bi.priority == BehaviorInstructionPriority.PROJECT_CONVENTION
        assert bi.conflict_policy == BehaviorConflictPolicy.HIGHER_PRIORITY_WINS
        assert bi.rule, f"{bi.name} must have a rule body"
        assert bi.rationale, f"{bi.name} must have a rationale"
        assert bi.applies_to, f"{bi.name} must declare applies_to terms"


def test_factory_names_are_unique_and_stable() -> None:
    names = [factory("ws", None).name for factory in ENFORCEMENT_FACTORIES]
    assert len(names) == len(set(names)), "factory names must be unique"
    assert "applies-to-checklist-must-be-stated-verbatim" in names
    assert "verification-claims-must-cite-prod-evidence" in names
    assert "memory-write-is-not-done-until-candidates-resolved" in names


def test_factories_registered_in_discipline_and_pinned() -> None:
    for factory in ENFORCEMENT_FACTORIES:
        assert factory in DISCIPLINE_FACTORIES, f"{factory.__name__} not in DISCIPLINE_FACTORIES"
        assert factory in PINNED_DISCIPLINE_FACTORIES, (
            f"{factory.__name__} not in PINNED_DISCIPLINE_FACTORIES"
        )


def test_rules_contain_trigger_action_key_invariant_sections() -> None:
    for factory in ENFORCEMENT_FACTORIES:
        rule = factory("ws", None).rule
        assert "TRIGGER" in rule, f"{factory.__name__} rule missing TRIGGER section"
        assert "ACTION" in rule, f"{factory.__name__} rule missing ACTION section"
        assert "KEY INVARIANT" in rule, f"{factory.__name__} rule missing KEY INVARIANT"


def test_payload_idempotent_under_repeated_calls() -> None:
    for factory in ENFORCEMENT_FACTORIES:
        a = factory("ws", "ep1")
        b = factory("ws", "ep1")
        assert a.name == b.name
        assert a.rule == b.rule
        assert a.applies_to == b.applies_to
        assert a.rationale == b.rationale


def test_applies_to_lists_target_action_keywords() -> None:
    checklist = applies_to_checklist_verbatim_instruction("ws", None)
    assert "git commit" in checklist.applies_to
    assert "deploy" in checklist.applies_to
    assert "memory_write_decision" in checklist.applies_to

    verification = verification_claims_cite_evidence_instruction("ws", None)
    assert any("verification" in t for t in verification.applies_to)

    resolve = memory_write_resolve_candidates_instruction("ws", None)
    assert "candidates_written field" in resolve.applies_to
    assert "capability_suggestions field" in resolve.applies_to
