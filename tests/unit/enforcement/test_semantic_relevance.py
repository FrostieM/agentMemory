"""Unit tests for the semantic-layer relevance filter."""

from __future__ import annotations

from agent_memory_lite.enforcement.rule_loader import EnforcementRule
from agent_memory_lite.enforcement.semantic_relevance import (
    filter_relevant,
    is_rule_relevant,
)


def _rule(rule_id: str, applies_to: list[str]) -> EnforcementRule:
    return EnforcementRule(
        id=rule_id,
        name=f"rule-{rule_id}",
        rule="body",
        level="semantic",
        applies_to=applies_to,
    )


def test_empty_applies_to_always_relevant() -> None:
    assert is_rule_relevant(_rule("beh_1", []), "Edit") is True


def test_response_only_rule_skipped_for_tool_calls() -> None:
    """A rule about response wording does not apply to a tool call."""
    rule = _rule("beh_r", ["first sentence of response", "verbatim"])
    assert is_rule_relevant(rule, "Edit") is False


def test_response_token_but_also_tool_match_keeps_rule() -> None:
    """A rule that mentions both response AND a tool keeps the tool side."""
    rule = _rule("beh_r", ["response when running Edit", "edit"])
    # 'edit' is a tool synonym for Edit, so the rule is kept.
    assert is_rule_relevant(rule, "Edit") is True


def test_tool_synonym_match_for_bash() -> None:
    rule = _rule("beh_g", ["git commit", "shipping to main"])
    assert is_rule_relevant(rule, "Bash") is True


def test_edit_synonym_match() -> None:
    rule = _rule("beh_e", ["before Edit tool", "code editing workflow"])
    assert is_rule_relevant(rule, "Edit") is True


def test_memory_tool_keyword_match() -> None:
    rule = _rule("beh_d", ["memory_write kind=decision", "architectural decisions"])
    assert is_rule_relevant(rule, "memory_write") is True


def test_mcp_prefixed_memory_tool_match() -> None:
    rule = _rule("beh_d", ["memory_write kind=decision"])
    assert is_rule_relevant(rule, "mcp__agent-memory-lite__memory_write") is True


def test_unmatched_applies_to_still_relevant_default() -> None:
    """Conservative default: when uncertain, keep the rule and let the LLM judge."""
    rule = _rule("beh_u", ["before Edit tool"])
    # Bash call against an Edit-oriented rule: the LLM gets to decide.
    assert is_rule_relevant(rule, "Bash") is True


def test_filter_relevant_drops_response_only_rules() -> None:
    rules = [
        _rule("beh_keep", ["before Edit tool"]),
        _rule("beh_drop", ["first sentence of response"]),
        _rule("beh_keep2", []),
    ]
    out = filter_relevant(rules, tool_name="Edit")
    assert {r.id for r in out} == {"beh_keep", "beh_keep2"}
