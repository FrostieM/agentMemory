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


@pytest.fixture(autouse=True)
def _allow_tmp_transcripts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Allow ``tmp_path`` as a transcript root for hermetic tests.

    The 1.2.1 transcript-path allowlist (M3) restricts reads to
    ``~/.claude/`` plus directories listed in
    ``AGENT_MEMORY_TRANSCRIPT_ROOTS``. Pytest's ``tmp_path`` is in the
    OS temp dir, so each test in this module needs the env override.
    Tests that EXERCISE the allowlist (``test_blocks_path_outside_*``)
    explicitly delete the env var with ``monkeypatch.delenv``.
    """
    monkeypatch.setenv("AGENT_MEMORY_TRANSCRIPT_ROOTS", str(tmp_path))


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


def test_blocks_path_outside_allowed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """1.2.1 (M3): a transcript_path that does not resolve under
    ``~/.claude/`` (or AGENT_MEMORY_TRANSCRIPT_ROOTS) is rejected,
    even when the file exists and contains a valid recent assistant
    turn. Without this check, a malicious hook caller could point at
    ``/etc/passwd`` (or any other readable file) and have its tail
    bytes embedded as the agent claim in an episode."""
    monkeypatch.delenv("AGENT_MEMORY_TRANSCRIPT_ROOTS", raising=False)
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    p = _write_jsonl(tmp_path, [_line("assistant", "x" * 60, ts=now - timedelta(minutes=2))])
    # tmp_path is the OS tmp dir, not ~/.claude/, so the call must
    # return None — proves the allowlist rejected the path even though
    # the JSONL is structurally valid and within the time window.
    assert find_last_assistant_text(p, now=now) is None


def test_env_var_rejects_relative_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """1.2.1 (M3c): a relative path in AGENT_MEMORY_TRANSCRIPT_ROOTS
    resolves against the hook caller's cwd which is non-deterministic
    across forks. The override must reject relative entries silently
    so they don't accidentally extend the allowlist depending on
    where the hook fired from."""
    # tmp_path itself is absolute; pass JUST the basename as a
    # relative path. With the relative-path filter, this entry is
    # dropped from the allowlist and the call falls back to the
    # default ~/.claude/ allowlist — which excludes tmp_path → reject.
    monkeypatch.setenv("AGENT_MEMORY_TRANSCRIPT_ROOTS", tmp_path.name)
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    p = _write_jsonl(
        tmp_path,
        [_line("assistant", "x" * 60, ts=now - timedelta(minutes=2))],
    )
    assert find_last_assistant_text(p, now=now) is None


def test_respects_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """1.2.1 (M3): explicit AGENT_MEMORY_TRANSCRIPT_ROOTS override
    re-allows a directory the default ``~/.claude/`` allowlist would
    have rejected. Each path in the env value is os.pathsep-separated
    and supports tilde expansion."""
    monkeypatch.setenv("AGENT_MEMORY_TRANSCRIPT_ROOTS", str(tmp_path))
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    p = _write_jsonl(
        tmp_path,
        [_line("assistant", "claim that should be readable", ts=now - timedelta(minutes=2))],
    )
    turn = find_last_assistant_text(p, now=now)
    assert turn is not None
    assert turn.text == "claim that should be readable"
