"""Unit tests for the semantic Ollama-judged enforcement layer."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from agent_memory_lite.enforcement.rule_loader import EnforcementRule
from agent_memory_lite.enforcement.semantic_review import check_semantic


def _rule(
    rule_id: str,
    *,
    level: str = "semantic",
    applies_to: list[str] | None = None,
) -> EnforcementRule:
    return EnforcementRule(
        id=rule_id,
        name=f"rule-{rule_id}",
        rule="Do not push to main without explicit operator approval.",
        level=level,
        applies_to=applies_to if applies_to is not None else ["before commit"],
    )


@pytest.fixture
def patched_ollama() -> Iterator[list[str]]:
    """Patch the async Ollama caller with an injectable response queue."""
    responses: list[str] = []

    async def _fake_call(prompt: str, *, base_url: str, model: str, timeout: float) -> str:
        del prompt, base_url, model, timeout
        return responses.pop(0) if responses else ""

    with patch(
        "agent_memory_lite.enforcement.semantic_review._call_ollama_async",
        side_effect=_fake_call,
    ):
        yield responses


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
        [_rule("beh_s", applies_to=["git commit"])],
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


def test_multiple_rules_each_judged_independently_in_parallel(
    patched_ollama: list[str],
) -> None:
    """asyncio.gather pulls all responses from the queue in order."""
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


def test_response_only_rule_filtered_out_before_ollama(
    patched_ollama: list[str],
) -> None:
    """Rules about response wording must not pay an Ollama call."""
    rule = _rule(
        "beh_response",
        applies_to=["first sentence of response", "verbatim opener"],
    )
    violations = check_semantic(
        [rule],
        tool_name="Edit",
        tool_input={},
        trail=[],
        base_url="http://x",
        model="m",
    )
    # No Ollama call was made — queue stays full / empty as it was.
    assert violations == []
    assert patched_ollama == []
