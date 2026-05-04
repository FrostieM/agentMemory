"""Unit tests for v1.10 transcript_pair_extractor.

Synthesises a tiny Claude Code-shaped JSONL fixture per test rather
than depending on the user's real transcripts, so the tests are
hermetic and CI-safe.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from transcript_pair_extractor import (
    AssistantTurn,
    find_last_assistant_text,
)


def _line(role: str, text: str, *, ts: datetime, uuid: str = "u1") -> str:
    """Build a Claude Code transcript JSONL line for testing."""
    obj = {
        "type": role,
        "uuid": uuid,
        "timestamp": ts.isoformat(),
        "message": {
            "role": role,
            "content": [{"type": "text", "text": text}],
        },
    }
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _write_jsonl(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "transcript.jsonl"
    p.write_text("".join(lines), encoding="utf-8")
    return p


def test_finds_recent_assistant_text(tmp_path: Path) -> None:
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    lines = [
        _line("user", "what's up", ts=now - timedelta(minutes=5)),
        _line("assistant", "the system uses SQLite + LanceDB", ts=now - timedelta(minutes=3)),
        _line("user", "ok", ts=now - timedelta(minutes=2)),
    ]
    p = _write_jsonl(tmp_path, lines)
    turn = find_last_assistant_text(p, now=now, window_minutes=30)
    assert turn is not None
    assert isinstance(turn, AssistantTurn)
    assert turn.text == "the system uses SQLite + LanceDB"


def test_returns_none_when_no_assistant_turn(tmp_path: Path) -> None:
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    lines = [_line("user", "hello", ts=now - timedelta(minutes=1))]
    p = _write_jsonl(tmp_path, lines)
    assert find_last_assistant_text(p, now=now) is None


def test_window_filter_drops_stale_turn(tmp_path: Path) -> None:
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    lines = [
        _line(
            "assistant",
            "this claim is from yesterday",
            ts=now - timedelta(hours=10),
        ),
    ]
    p = _write_jsonl(tmp_path, lines)
    assert find_last_assistant_text(p, now=now, window_minutes=30) is None


def test_skips_tool_use_blocks(tmp_path: Path) -> None:
    """Tool_use blocks are not 'claims' the user can correct."""
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    obj = {
        "type": "assistant",
        "uuid": "u",
        "timestamp": (now - timedelta(minutes=2)).isoformat(),
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {}},
                {"type": "text", "text": "actual visible response"},
            ],
        },
    }
    lines = [json.dumps(obj) + "\n"]
    p = _write_jsonl(tmp_path, lines)
    turn = find_last_assistant_text(p, now=now)
    assert turn is not None
    assert turn.text == "actual visible response"


def test_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert find_last_assistant_text(tmp_path / "nonexistent.jsonl") is None


def test_returns_none_on_corrupt_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "corrupt.jsonl"
    p.write_text("{not valid json\n", encoding="utf-8")
    assert find_last_assistant_text(p) is None


def test_uses_most_recent_text_when_multiple_assistant_turns(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    lines = [
        _line(
            "assistant",
            "earlier claim",
            ts=now - timedelta(minutes=15),
            uuid="u_early",
        ),
        _line("user", "ok", ts=now - timedelta(minutes=14)),
        _line(
            "assistant",
            "later claim",
            ts=now - timedelta(minutes=2),
            uuid="u_late",
        ),
    ]
    p = _write_jsonl(tmp_path, lines)
    turn = find_last_assistant_text(p, now=now)
    assert turn is not None
    assert turn.text == "later claim"
    assert turn.uuid == "u_late"


def test_handles_string_content_alongside_list_content(tmp_path: Path) -> None:
    """Some Claude Code variants serialize content as a plain string."""
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    obj = {
        "type": "assistant",
        "uuid": "u",
        "timestamp": (now - timedelta(minutes=2)).isoformat(),
        "message": {"role": "assistant", "content": "string-form claim"},
    }
    p = tmp_path / "transcript.jsonl"
    p.write_text(json.dumps(obj) + "\n", encoding="utf-8")
    turn = find_last_assistant_text(p, now=now)
    assert turn is not None
    assert turn.text == "string-form claim"


@pytest.mark.parametrize(
    "ts_string",
    [
        "2026-05-04T12:00:00Z",
        "2026-05-04T12:00:00+00:00",
        "2026-05-04T12:00:00.123456+00:00",
    ],
)
def test_parses_iso_timestamp_variants(tmp_path: Path, ts_string: str) -> None:
    obj = {
        "type": "assistant",
        "uuid": "u",
        "timestamp": ts_string,
        "message": {"role": "assistant", "content": [{"type": "text", "text": "x"}]},
    }
    p = tmp_path / "transcript.jsonl"
    p.write_text(json.dumps(obj) + "\n", encoding="utf-8")
    # Set "now" 1 minute after the timestamp so the window covers it.
    now = datetime(2026, 5, 4, 12, 1, 0, tzinfo=UTC)
    turn = find_last_assistant_text(p, now=now)
    assert turn is not None
    assert turn.text == "x"
