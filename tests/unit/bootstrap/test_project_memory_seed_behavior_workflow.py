"""Unit tests for workflow-discipline seed factories."""

from __future__ import annotations

from agent_memory_lite.bootstrap.project_memory_seed_behavior_workflow import (
    memory_first_before_edit_instruction,
    no_unauthorized_git_push_instruction,
)
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)


def test_memory_first_rule_closes_fallback_loophole() -> None:
    bi = memory_first_before_edit_instruction("ws", "ep_src")
    assert bi.name == "Memory-first before reading or editing source"
    rule = bi.rule
    assert "TRIGGER" in rule
    assert "ACTION" in rule
    assert "KEY INVARIANT" in rule
    assert "memory_ingest_file" in rule, "rule must require ingest, not fallback"
    assert "memory_symbol_history" in rule, "rule must require symbol_history for context"
    assert "memory_breaking_changes" in rule, "rule must require breaking_changes for context"
    assert "WHAT" in rule, "rule must promise WHAT context"
    assert "WHEN" in rule, "rule must promise WHEN context"


def test_memory_first_payload_metadata() -> None:
    bi = memory_first_before_edit_instruction("ws", "ep_src")
    assert bi.workspace_id == "ws"
    assert bi.source_episode_id == "ep_src"
    assert bi.source_type == "seed_bootstrap"
    assert bi.active is True
    assert bi.confidence >= 0.9
    assert bi.kind == BehaviorInstructionKind.WORKFLOW_PREFERENCE
    assert bi.scope == BehaviorInstructionScope.WORKSPACE
    assert bi.priority == BehaviorInstructionPriority.PROJECT_CONVENTION
    assert bi.conflict_policy == BehaviorConflictPolicy.HIGHER_PRIORITY_WINS


def test_memory_first_applies_to_covers_edit_and_write() -> None:
    bi = memory_first_before_edit_instruction("ws", None)
    assert "before Edit tool" in bi.applies_to
    assert "before Write tool" in bi.applies_to
    assert "before modifying a function" in bi.applies_to
    assert "before Read tool" in bi.applies_to


def test_no_git_push_rule_blocks_unauthorized_shipping() -> None:
    bi = no_unauthorized_git_push_instruction("ws", "ep_src")
    assert bi.name == "No git commit/push/CI without explicit operator permission"
    rule = bi.rule
    assert "git commit" in rule
    assert "git push" in rule
    assert "shipping" in rule.lower() or "push" in rule.lower()
    assert "git commit" in bi.applies_to
    assert "git push" in bi.applies_to


def test_factories_are_idempotent() -> None:
    for factory in (memory_first_before_edit_instruction, no_unauthorized_git_push_instruction):
        a = factory("ws", "ep1")
        b = factory("ws", "ep1")
        assert a.name == b.name
        assert a.rule == b.rule
        assert a.applies_to == b.applies_to
