"""Unit tests for memory-audit prompt analyzer."""

from __future__ import annotations

import json
from pathlib import Path

from agent_memory_lite.extraction.memory_audit_prompt import (
    TurnToolStats,
    analyze_last_assistant_turn,
    classify_tool,
    decide_audit,
)


def test_classify_builtin_mutation_tools() -> None:
    assert classify_tool("Edit") == "file_mutation"
    assert classify_tool("Write") == "file_mutation"
    assert classify_tool("Bash") == "file_mutation"
    assert classify_tool("NotebookEdit") == "file_mutation"


def test_classify_builtin_read_tools() -> None:
    assert classify_tool("Read") == "file_read"
    assert classify_tool("Grep") == "file_read"
    assert classify_tool("Glob") == "file_read"


def test_classify_memory_writes_bare_and_mcp_prefixed() -> None:
    assert classify_tool("memory_ingest_episode") == "memory_write"
    assert classify_tool("memory_write_decision") == "memory_write"
    assert classify_tool("memory_record_with_evidence") == "memory_write"
    # MCP-prefixed form (Claude Code shows tools as mcp__server__tool)
    assert classify_tool("mcp__agent-memory-lite__memory_ingest_episode") == "memory_write"
    assert classify_tool("mcp__agent-memory-lite__memory_link_capability") == "memory_write"
    assert classify_tool("mcp__agent-memory-lite__memory_upsert_concept") == "memory_write"


def test_classify_memory_reads() -> None:
    assert classify_tool("memory_get_context") == "memory_read"
    assert classify_tool("memory_search") == "memory_read"
    assert classify_tool("memory_file_digest") == "memory_read"
    assert classify_tool("mcp__agent-memory-lite__memory_find_symbols") == "memory_read"


def test_classify_unknown_tool_is_other() -> None:
    assert classify_tool("SomeRandomTool") == "other"
    assert classify_tool("WebFetch") == "other"


def test_decide_audit_none_stats_returns_no_inject() -> None:
    d = decide_audit(None)
    assert d.inject is False
    assert d.prompt == ""


def test_decide_audit_no_mutations_returns_no_inject() -> None:
    stats = TurnToolStats(
        file_mutations=0,
        file_reads=5,
        memory_writes=0,
        memory_reads=2,
        other=0,
        tool_names=("Read", "Read", "Grep", "memory_search", "Glob"),
    )
    d = decide_audit(stats)
    assert d.inject is False


def test_decide_audit_with_memory_writes_returns_no_inject() -> None:
    stats = TurnToolStats(
        file_mutations=3,
        file_reads=1,
        memory_writes=1,
        memory_reads=0,
        other=0,
        tool_names=("Edit", "Edit", "Bash", "Read", "memory_ingest_episode"),
    )
    d = decide_audit(stats)
    assert d.inject is False, "writes were present — no audit needed"


def test_decide_audit_mutations_without_writes_injects() -> None:
    stats = TurnToolStats(
        file_mutations=4,
        file_reads=2,
        memory_writes=0,
        memory_reads=1,
        other=0,
        tool_names=("Edit", "Edit", "Bash", "Bash", "Read", "Read", "memory_search"),
    )
    d = decide_audit(stats)
    assert d.inject is True
    assert "[memory-audit]" in d.prompt
    assert "0 memory writes" in d.prompt
    assert "memory_ingest_episode" in d.prompt
    assert "4 file mutations" in d.prompt


def test_decide_audit_respects_min_mutations_threshold() -> None:
    stats = TurnToolStats(
        file_mutations=1,
        file_reads=0,
        memory_writes=0,
        memory_reads=0,
        other=0,
        tool_names=("Edit",),
    )
    d_default = decide_audit(stats)
    assert d_default.inject is False, "default min_mutations=2, 1 should not trigger"
    d_lower = decide_audit(stats, min_mutations=1)
    assert d_lower.inject is True


def _write_transcript(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_analyze_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert analyze_last_assistant_turn(tmp_path / "nonexistent.jsonl") is None


def test_analyze_returns_none_when_no_assistant_turn(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    _write_transcript(f, [{"message": {"role": "user", "content": "hi"}}])
    assert analyze_last_assistant_turn(f) is None


def test_analyze_counts_tool_use_in_last_assistant_turn(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    _write_transcript(
        f,
        [
            {"message": {"role": "user", "content": "do work"}},
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "ok"},
                        {"type": "tool_use", "name": "Read", "id": "r1"},
                        {"type": "tool_use", "name": "Edit", "id": "e1"},
                        {"type": "tool_use", "name": "Edit", "id": "e2"},
                        {"type": "tool_use", "name": "Bash", "id": "b1"},
                    ],
                }
            },
            {"message": {"role": "user", "content": "next"}},
        ],
    )
    stats = analyze_last_assistant_turn(f)
    assert stats is not None
    assert stats.file_mutations == 3, "2 Edit + 1 Bash"
    assert stats.file_reads == 1
    assert stats.memory_writes == 0


def test_analyze_finds_assistant_turn_even_when_later_user_turn_exists(tmp_path: Path) -> None:
    """Walks from end and returns the LAST assistant turn regardless of trailing user turn."""
    f = tmp_path / "t.jsonl"
    _write_transcript(
        f,
        [
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Edit", "id": "e1"}],
                }
            },
            {"message": {"role": "user", "content": "thanks"}},
        ],
    )
    stats = analyze_last_assistant_turn(f)
    assert stats is not None
    assert stats.file_mutations == 1


def test_analyze_skips_malformed_jsonl_lines(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    f.write_text(
        "not json\n"
        + json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Edit", "id": "e1"}],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stats = analyze_last_assistant_turn(f)
    assert stats is not None
    assert stats.file_mutations == 1


def test_end_to_end_assistant_with_writes_no_audit(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    _write_transcript(
        f,
        [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Edit", "id": "e1"},
                        {"type": "tool_use", "name": "Edit", "id": "e2"},
                        {
                            "type": "tool_use",
                            "name": "mcp__agent-memory-lite__memory_ingest_episode",
                            "id": "m1",
                        },
                    ],
                }
            }
        ],
    )
    stats = analyze_last_assistant_turn(f)
    d = decide_audit(stats)
    assert d.inject is False, "writes present, audit should NOT fire"


def test_end_to_end_assistant_without_writes_audit_fires(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    _write_transcript(
        f,
        [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Edit", "id": "e1"},
                        {"type": "tool_use", "name": "Edit", "id": "e2"},
                        {"type": "tool_use", "name": "Bash", "id": "b1"},
                    ],
                }
            }
        ],
    )
    stats = analyze_last_assistant_turn(f)
    d = decide_audit(stats)
    assert d.inject is True
    assert "memory_ingest_episode" in d.prompt
