from __future__ import annotations

from scripts.inject_memory_context import _should_emit_context


def test_hook_dedupe_suppresses_immediate_duplicate(tmp_path) -> None:
    cache_path = tmp_path / "hook-cache.json"
    event = {
        "session_id": "session-1",
        "cwd": "/repo",
        "prompt": "same prompt",
    }

    assert _should_emit_context(
        event,
        workspace="default",
        prompt="same prompt",
        cache_path=cache_path,
        ttl_seconds=10,
    )
    assert not _should_emit_context(
        event,
        workspace="default",
        prompt="same prompt",
        cache_path=cache_path,
        ttl_seconds=10,
    )


def test_hook_dedupe_can_be_disabled(tmp_path) -> None:
    cache_path = tmp_path / "hook-cache.json"
    event = {"session_id": "session-1", "prompt": "same prompt"}

    assert _should_emit_context(
        event,
        workspace="default",
        prompt="same prompt",
        cache_path=cache_path,
        ttl_seconds=0,
    )
    assert _should_emit_context(
        event,
        workspace="default",
        prompt="same prompt",
        cache_path=cache_path,
        ttl_seconds=0,
    )
