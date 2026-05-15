"""Unit tests for the semantic Ollama-judged enforcement layer."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from agent_memory_lite.enforcement.rule_loader import EnforcementRule
from agent_memory_lite.enforcement.semantic_review import (
    _parse_verdict,
    check_semantic,
)


def _rule(rule_id: str, level: str = "semantic") -> EnforcementRule:
    return EnforcementRule(
        id=rule_id,
        name=f"rule-{rule_id}",
        rule="Do not push to main without explicit operator approval.",
        level=level,
        applies_to=["enforcement:semantic"],
    )


@pytest.fixture
def patched_ollama() -> Iterator[list[str]]:
    """Patch ``_call_ollama`` with an injectable response queue."""
    responses: list[str] = []

    def _fake_call(prompt: str, *, base_url: str, model: str, timeout: float) -> str:
        del prompt, base_url, model, timeout
        return responses.pop(0) if responses else ""

    with patch(
        "agent_memory_lite.enforcement.semantic_review._call_ollama",
        side_effect=_fake_call,
    ):
        yield responses


def test_parse_verdict_clean_json() -> None:
    violates, why = _parse_verdict('{"violates": true, "why": "broke rule X"}')
    assert violates is True
    assert why == "broke rule X"


def test_parse_verdict_false() -> None:
    violates, _ = _parse_verdict('{"violates": false, "why": "ok"}')
    assert violates is False


def test_parse_verdict_fences_stripped() -> None:
    violates, why = _parse_verdict('```json\n{"violates": true, "why": "fenced"}\n```')
    assert violates is True
    assert why == "fenced"


def test_parse_verdict_invalid_json_defaults_to_false() -> None:
    assert _parse_verdict("not json at all") == (False, "")


def test_parse_verdict_empty_string() -> None:
    assert _parse_verdict("") == (False, "")


def test_parse_verdict_non_object_defaults_to_false() -> None:
    assert _parse_verdict("[true]") == (False, "")


def test_no_semantic_rules_returns_empty(patched_ollama: list[str]) -> None:
    del patched_ollama
    violations = check_semantic(
        [],
        tool_name="Edit",
        tool_input={},
        trail=[],
        base_url="http://x",
        model="m",
    )
    assert violations == []


def test_mechanical_rules_skipped_by_semantic_dispatcher(
    patched_ollama: list[str],
) -> None:
    del patched_ollama
    violations = check_semantic(
        [_rule("beh_m", level="mechanical")],
        tool_name="Edit",
        tool_input={},
        trail=[],
        base_url="http://x",
        model="m",
    )
    assert violations == []


def test_ollama_says_violates_blocks(patched_ollama: list[str]) -> None:
    patched_ollama.append('{"violates": true, "why": "pushes main"}')
    violations = check_semantic(
        [_rule("beh_s")],
        tool_name="Bash",
        tool_input={"command": "git push"},
        trail=[],
        base_url="http://x",
        model="m",
    )
    assert len(violations) == 1
    assert violations[0].why == "pushes main"
    assert violations[0].enforcement_level == "semantic"


def test_ollama_says_ok_passes(patched_ollama: list[str]) -> None:
    patched_ollama.append('{"violates": false, "why": "ok"}')
    violations = check_semantic(
        [_rule("beh_s")],
        tool_name="Edit",
        tool_input={},
        trail=[],
        base_url="http://x",
        model="m",
    )
    assert violations == []


def test_ollama_unreachable_fails_open(patched_ollama: list[str]) -> None:
    """Empty response (transport error simulation) MUST NOT block the call."""
    patched_ollama.append("")
    violations = check_semantic(
        [_rule("beh_s")],
        tool_name="Edit",
        tool_input={},
        trail=[],
        base_url="http://x",
        model="m",
    )
    assert violations == []


def test_multiple_rules_each_judged_independently(
    patched_ollama: list[str],
) -> None:
    patched_ollama.extend(
        [
            '{"violates": true, "why": "rule a"}',
            '{"violates": false, "why": "rule b ok"}',
            '{"violates": true, "why": "rule c"}',
        ]
    )
    violations = check_semantic(
        [_rule("beh_a"), _rule("beh_b"), _rule("beh_c")],
        tool_name="Edit",
        tool_input={},
        trail=[],
        base_url="http://x",
        model="m",
    )
    assert {v.rule_id for v in violations} == {"beh_a", "beh_c"}


def test_missing_why_falls_back_to_placeholder(
    patched_ollama: list[str],
) -> None:
    patched_ollama.append('{"violates": true}')
    violations = check_semantic(
        [_rule("beh_s")],
        tool_name="Edit",
        tool_input={},
        trail=[],
        base_url="http://x",
        model="m",
    )
    assert len(violations) == 1
    assert "without explanation" in violations[0].why
