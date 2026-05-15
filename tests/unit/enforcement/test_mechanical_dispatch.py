"""Unit tests for the mechanical dispatcher."""

from __future__ import annotations

from agent_memory_lite.enforcement.mechanical_decision_provenance import (
    RULE_TAG as DECISION_PROVENANCE_TAG,
)
from agent_memory_lite.enforcement.mechanical_dispatch import check_mechanical
from agent_memory_lite.enforcement.mechanical_magic_number import (
    RULE_TAG as MAGIC_NUMBER_TAG,
)
from agent_memory_lite.enforcement.rule_loader import EnforcementRule


def _rule(rule_id: str, tag: str, level: str = "mechanical") -> EnforcementRule:
    return EnforcementRule(
        id=rule_id,
        name=tag,
        rule="rule body",
        level=level,
        applies_to=[tag, f"enforcement:{level}"],
    )


def test_no_rules_no_violations() -> None:
    assert check_mechanical([], tool_name="Edit", tool_input={}) == []


def test_magic_number_rule_blocks_edit_in_strategy_path() -> None:
    rules = [_rule("beh_mn", MAGIC_NUMBER_TAG)]
    violations = check_mechanical(
        rules,
        tool_name="Edit",
        tool_input={
            "file_path": "src/strategy/x.py",
            "new_string": "if confidence > 0.85:\n    pass",
        },
    )
    assert len(violations) == 1
    assert violations[0].rule_id == "beh_mn"
    assert violations[0].enforcement_level == "mechanical"


def test_magic_number_rule_does_not_block_unrelated_file() -> None:
    rules = [_rule("beh_mn", MAGIC_NUMBER_TAG)]
    violations = check_mechanical(
        rules,
        tool_name="Edit",
        tool_input={
            "file_path": "src/api/health.py",
            "new_string": "if confidence > 0.85:\n    pass",
        },
    )
    assert violations == []


def test_magic_number_rule_ignores_non_edit_write_tool() -> None:
    rules = [_rule("beh_mn", MAGIC_NUMBER_TAG)]
    violations = check_mechanical(
        rules,
        tool_name="Read",
        tool_input={"file_path": "src/strategy/x.py"},
    )
    assert violations == []


def test_decision_provenance_rule_blocks_bare_write_decision() -> None:
    rules = [_rule("beh_dp", DECISION_PROVENANCE_TAG)]
    violations = check_mechanical(
        rules,
        tool_name="memory_write_decision",
        tool_input={"title": "T", "decision_text": "..."},
    )
    assert len(violations) == 1
    assert violations[0].rule_id == "beh_dp"


def test_decision_provenance_rule_allows_orphan_marked_decision() -> None:
    rules = [_rule("beh_dp", DECISION_PROVENANCE_TAG)]
    violations = check_mechanical(
        rules,
        tool_name="memory_write_decision",
        tool_input={"title": "T", "allow_orphan": True},
    )
    assert violations == []


def test_rule_without_detector_tag_is_skipped() -> None:
    rules = [
        EnforcementRule(
            id="beh_x",
            name="rule with no detector tag",
            rule="body",
            level="mechanical",
            applies_to=["enforcement:mechanical", "no:matching:detector"],
        )
    ]
    assert check_mechanical(rules, tool_name="Edit", tool_input={}) == []


def test_semantic_level_rule_skipped_by_mechanical_dispatch() -> None:
    rules = [_rule("beh_s", MAGIC_NUMBER_TAG, level="semantic")]
    assert (
        check_mechanical(
            rules,
            tool_name="Edit",
            tool_input={
                "file_path": "src/strategy/x.py",
                "new_string": "if confidence > 0.85:\n    pass",
            },
        )
        == []
    )


def test_multiple_rules_yield_multiple_violations() -> None:
    rules = [
        _rule("beh_mn", MAGIC_NUMBER_TAG),
        _rule("beh_dp", DECISION_PROVENANCE_TAG),
    ]
    # Magic-number rule does NOT fire on memory_write_decision call.
    violations = check_mechanical(
        rules,
        tool_name="memory_write_decision",
        tool_input={"title": "T"},
    )
    assert {v.rule_id for v in violations} == {"beh_dp"}


def test_edit_payload_with_content_field_also_checked() -> None:
    """Write tool uses 'content', not 'new_string'."""
    rules = [_rule("beh_mn", MAGIC_NUMBER_TAG)]
    violations = check_mechanical(
        rules,
        tool_name="Write",
        tool_input={
            "file_path": "src/strategy/x.py",
            "content": "if margin > 0.42:\n    pass",
        },
    )
    assert len(violations) == 1
