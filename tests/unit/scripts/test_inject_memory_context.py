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


def test_global_fallback_bootstraps_db_and_registers(tmp_path, monkeypatch) -> None:
    """First call to _ensure_global_fallback creates the DB and adds an
    entry to the workspaces registry. Subsequent calls are idempotent
    and don't reinitialize."""
    sandbox = tmp_path / "global"
    registry = tmp_path / "workspaces.json"
    monkeypatch.setenv("AGENT_MEMORY_FALLBACK_DIR", str(sandbox))
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry))
    monkeypatch.setenv("AGENT_MEMORY_FALLBACK_WORKSPACE", "qa-fallback")

    # Reload so module-level constants pick up the patched env.
    import importlib  # noqa: PLC0415

    import scripts.inject_memory_context as hook_module  # noqa: PLC0415

    importlib.reload(hook_module)
    result = hook_module._ensure_global_fallback()

    assert result is not None
    assert result["workspace_id"] == "qa-fallback"
    assert (sandbox / "memory.db").exists()
    assert registry.exists()
    import json  # noqa: PLC0415

    data = json.loads(registry.read_text(encoding="utf-8"))
    ids = [w["id"] for w in data["workspaces"]]
    assert "qa-fallback" in ids

    # Second invocation must not duplicate the registry entry.
    hook_module._ensure_global_fallback()
    data = json.loads(registry.read_text(encoding="utf-8"))
    ids = [w["id"] for w in data["workspaces"]]
    assert ids.count("qa-fallback") == 1
