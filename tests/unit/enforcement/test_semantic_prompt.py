"""Unit tests for the semantic-review prompt builder."""

from __future__ import annotations

from agent_memory_lite.enforcement.semantic_prompt import build_review_prompt


def test_prompt_contains_rule_text() -> None:
    prompt = build_review_prompt(
        rule_text="Do not push without operator approval.",
        tool_name="Bash",
        tool_input={"command": "git push origin main"},
        trail=[],
    )
    assert "Do not push without operator approval." in prompt
    assert "Bash" in prompt
    assert "git push origin main" in prompt


def test_prompt_contains_trail_tail() -> None:
    trail = [f"Tool{i}" for i in range(30)]
    prompt = build_review_prompt(rule_text="r", tool_name="Edit", tool_input={}, trail=trail)
    assert "Tool29" in prompt
    # First items beyond last-20 tail should NOT be in the prompt.
    assert "Tool0" not in prompt
    assert "Tool5" not in prompt


def test_prompt_empty_trail_uses_placeholder() -> None:
    prompt = build_review_prompt(rule_text="r", tool_name="Edit", tool_input={}, trail=[])
    assert "(no prior tool calls)" in prompt


def test_prompt_truncates_oversized_payload() -> None:
    big_string = "X" * 5000
    prompt = build_review_prompt(
        rule_text="r",
        tool_name="Write",
        tool_input={"content": big_string},
        trail=[],
    )
    assert "<truncated>" in prompt
    assert len(prompt) < 6000


def test_prompt_compacts_only_known_fields() -> None:
    """Random extra fields don't get sent — only the documented ones."""
    prompt = build_review_prompt(
        rule_text="r",
        tool_name="Edit",
        tool_input={"file_path": "a.py", "new_string": "x", "secret": "abc"},
        trail=[],
    )
    assert "a.py" in prompt
    assert "new_string" in prompt
    assert "secret" not in prompt


def test_prompt_specifies_strict_json_reply_shape() -> None:
    prompt = build_review_prompt(rule_text="r", tool_name="Edit", tool_input={}, trail=[])
    assert "violates" in prompt
    assert "why" in prompt
    assert "JSON" in prompt
