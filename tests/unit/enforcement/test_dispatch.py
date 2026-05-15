"""Unit tests for the top-level PreToolUse dispatcher."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from agent_memory_lite.enforcement.dispatch import decide_with_rules
from agent_memory_lite.enforcement.mechanical_magic_number import (
    RULE_TAG as MAGIC_NUMBER_TAG,
)
from agent_memory_lite.enforcement.mechanical_read_before_edit import (
    RULE_TAG as READ_BEFORE_EDIT_TAG,
)
from agent_memory_lite.enforcement.rule_loader import EnforcementRule


def _mech_rule(tag: str) -> EnforcementRule:
    return EnforcementRule(
        id=f"beh_{tag}",
        name=f"rule-{tag}",
        rule="body",
        level="mechanical",
        applies_to=[tag, "enforcement:mechanical"],
    )


def _sem_rule(rule_id: str) -> EnforcementRule:
    return EnforcementRule(
        id=rule_id,
        name=f"rule-{rule_id}",
        rule="Do not push to main without explicit operator approval.",
        level="semantic",
        applies_to=["enforcement:semantic"],
    )


@pytest.fixture
def patched_ollama() -> Iterator[list[str]]:
    responses: list[str] = []

    def _fake_call(prompt: str, *, base_url: str, model: str, timeout: float) -> str:
        del prompt, base_url, model, timeout
        return responses.pop(0) if responses else ""

    with patch(
        "agent_memory_lite.enforcement.semantic_review._call_ollama",
        side_effect=_fake_call,
    ):
        yield responses


def test_no_rules_allow(patched_ollama: list[str]) -> None:
    del patched_ollama
    d = decide_with_rules(
        [],
        tool_name="Edit",
        tool_input={},
        trail=[],
        ollama_base_url=None,
        ollama_model=None,
    )
    assert d.allow is True


def test_mechanical_block_short_circuits_semantic(
    patched_ollama: list[str],
) -> None:
    """If mechanical fires we MUST NOT call Ollama."""
    rules = [_mech_rule(MAGIC_NUMBER_TAG), _sem_rule("beh_s")]
    d = decide_with_rules(
        rules,
        tool_name="Edit",
        tool_input={
            "file_path": "src/strategy/x.py",
            "new_string": "if confidence > 0.85:\n    pass",
        },
        trail=["Read"],
        ollama_base_url="http://x",
        ollama_model="m",
    )
    assert d.allow is False
    assert any(v.enforcement_level == "mechanical" for v in d.violations)
    # Ollama queue untouched.
    assert patched_ollama == []


def test_mechanical_pass_runs_semantic(patched_ollama: list[str]) -> None:
    patched_ollama.append('{"violates": true, "why": "pushes main"}')
    rules = [_sem_rule("beh_s")]
    d = decide_with_rules(
        rules,
        tool_name="Bash",
        tool_input={"command": "git push"},
        trail=[],
        ollama_base_url="http://x",
        ollama_model="m",
    )
    assert d.allow is False
    assert d.violations[0].enforcement_level == "semantic"


def test_all_layers_pass_allow(patched_ollama: list[str]) -> None:
    patched_ollama.append('{"violates": false, "why": "ok"}')
    rules = [_sem_rule("beh_s")]
    d = decide_with_rules(
        rules,
        tool_name="Edit",
        tool_input={"file_path": "src/a.py", "new_string": "x"},
        trail=["Read"],
        ollama_base_url="http://x",
        ollama_model="m",
    )
    assert d.allow is True


def test_semantic_skipped_when_ollama_not_configured(
    patched_ollama: list[str],
) -> None:
    rules = [_sem_rule("beh_s")]
    d = decide_with_rules(
        rules,
        tool_name="Bash",
        tool_input={"command": "git push"},
        trail=[],
        ollama_base_url=None,
        ollama_model=None,
    )
    assert d.allow is True
    assert patched_ollama == []


def test_trail_aware_mechanical_blocks_without_read(
    patched_ollama: list[str],
) -> None:
    del patched_ollama
    rules = [_mech_rule(READ_BEFORE_EDIT_TAG)]
    d = decide_with_rules(
        rules,
        tool_name="Edit",
        tool_input={"file_path": "src/x.py", "new_string": "x"},
        trail=[],
        ollama_base_url=None,
        ollama_model=None,
    )
    assert d.allow is False
    assert "Read" in d.violations[0].why


def test_trail_aware_mechanical_passes_after_read(
    patched_ollama: list[str],
) -> None:
    del patched_ollama
    rules = [_mech_rule(READ_BEFORE_EDIT_TAG)]
    d = decide_with_rules(
        rules,
        tool_name="Edit",
        tool_input={"file_path": "src/x.py", "new_string": "x"},
        trail=["Read"],
        ollama_base_url=None,
        ollama_model=None,
    )
    assert d.allow is True


def test_only_mechanical_rules_no_ollama_call(patched_ollama: list[str]) -> None:
    """When no semantic rules exist we must not attempt Ollama at all."""
    rules = [_mech_rule(MAGIC_NUMBER_TAG)]
    d = decide_with_rules(
        rules,
        tool_name="Edit",
        tool_input={"file_path": "src/api/x.py", "new_string": "ok = 1"},
        trail=["Read"],
        ollama_base_url="http://x",
        ollama_model="m",
    )
    assert d.allow is True
    assert patched_ollama == []
