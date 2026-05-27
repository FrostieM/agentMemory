"""Unit tests for the session-trail reader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_memory_lite.enforcement.session_trail import (
    has_called,
    read_prior_tool_calls,
)


def _write_jsonl(path: Path, messages: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(m) for m in messages) + "\n",
        encoding="utf-8",
    )


def _assistant_tool_use(name: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": name, "input": {}}],
        },
    }


def _assistant_text() -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
        },
    }


def _user_text() -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": "hi"},
    }


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    return tmp_path / "session.jsonl"


def test_missing_transcript_returns_empty(transcript: Path) -> None:
    assert read_prior_tool_calls(str(transcript)) == []


def test_none_path_returns_empty() -> None:
    assert read_prior_tool_calls(None) == []


def test_empty_string_path_returns_empty() -> None:
    assert read_prior_tool_calls("") == []


def test_assistant_tool_use_extracted(transcript: Path) -> None:
    _write_jsonl(
        transcript,
        [_user_text(), _assistant_tool_use("Read"), _assistant_tool_use("Edit")],
    )
    assert read_prior_tool_calls(str(transcript)) == ["Read", "Edit"]


def test_user_messages_ignored(transcript: Path) -> None:
    _write_jsonl(transcript, [_user_text(), _user_text()])
    assert read_prior_tool_calls(str(transcript)) == []


def test_assistant_text_only_no_tool_use(transcript: Path) -> None:
    _write_jsonl(transcript, [_assistant_text()])
    assert read_prior_tool_calls(str(transcript)) == []


def test_malformed_line_skipped(transcript: Path) -> None:
    transcript.write_text(
        "{not-json}\n" + json.dumps(_assistant_tool_use("Grep")) + "\n",
        encoding="utf-8",
    )
    assert read_prior_tool_calls(str(transcript)) == ["Grep"]


def test_lookback_window_trims_old_lines(transcript: Path) -> None:
    """When the file is longer than ``max_lines``, only the tail is read."""
    messages = [_assistant_tool_use(f"Tool{i}") for i in range(20)]
    _write_jsonl(transcript, messages)
    names = read_prior_tool_calls(str(transcript), max_lines=5)
    assert names == ["Tool15", "Tool16", "Tool17", "Tool18", "Tool19"]


def test_has_called_bare_name_match() -> None:
    assert has_called(["Read", "Edit"], "Read") is True
    assert has_called(["Read", "Edit"], "Write") is False


def test_has_called_mcp_prefixed_match() -> None:
    """MCP tool names appear prefixed; matcher accepts suffix match."""
    trail = ["mcp__agent-memory-lite__memory_impact_check"]
    assert has_called(trail, "memory_impact_check") is True


def test_has_called_no_candidates_returns_false() -> None:
    assert has_called(["Read"]) is False


def test_has_called_multiple_candidates_any_match() -> None:
    assert has_called(["Edit"], "Read", "Edit", "Write") is True


def test_mixed_message_types_only_assistant_tool_use(transcript: Path) -> None:
    _write_jsonl(
        transcript,
        [
            _user_text(),
            _assistant_text(),
            _assistant_tool_use("Glob"),
            _user_text(),
            _assistant_tool_use("Grep"),
        ],
    )
    assert read_prior_tool_calls(str(transcript)) == ["Glob", "Grep"]
