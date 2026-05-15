"""Unit tests for PreToolUse verdict types."""

from __future__ import annotations

from agent_memory_lite.enforcement.verdict import (
    HookDecision,
    RuleViolation,
    allow,
    block,
)


def test_allow_factory_produces_permissive_decision() -> None:
    decision = allow()
    assert decision.allow is True
    assert decision.violations == []
    assert decision.diagnostic == ""


def test_block_factory_records_violations() -> None:
    v = RuleViolation(
        rule_id="beh_abc",
        rule_name="no-magic-number",
        why="literal 0.85 in strategy/file.py",
        enforcement_level="mechanical",
    )
    decision = block([v])
    assert decision.allow is False
    assert decision.violations == [v]


def test_diagnostic_lists_rule_id_and_why() -> None:
    v = RuleViolation(
        rule_id="beh_abc",
        rule_name="no-magic-number",
        why="literal 0.85 in strategy/file.py",
        enforcement_level="mechanical",
    )
    text = block([v]).diagnostic
    assert "beh_abc" in text
    assert "no-magic-number" in text
    assert "literal 0.85" in text
    assert "mechanical" in text
    assert "retry" in text.lower()


def test_diagnostic_empty_when_allowed() -> None:
    assert HookDecision(allow=True, violations=[]).diagnostic == ""


def test_diagnostic_multiple_violations_one_line_each() -> None:
    violations = [
        RuleViolation("beh_1", "rule-a", "reason A", "mechanical"),
        RuleViolation("beh_2", "rule-b", "reason B", "semantic"),
    ]
    text = block(violations).diagnostic
    assert "beh_1" in text
    assert "beh_2" in text
    assert "rule-a" in text
    assert "rule-b" in text
    assert "reason A" in text
    assert "reason B" in text


def test_decision_is_frozen() -> None:
    v = RuleViolation("beh_1", "n", "w", "mechanical")
    try:
        v.rule_id = "beh_2"  # type: ignore[misc]
    except (AttributeError, TypeError):
        return
    raise AssertionError("RuleViolation must be frozen")
