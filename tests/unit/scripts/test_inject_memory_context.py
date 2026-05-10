from __future__ import annotations

import io
import json

from scripts.inject_memory_context import (
    _should_emit_context,
)


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


# ---------- registry helper ----------


def test_list_registry_entries_missing_file(tmp_path, monkeypatch) -> None:
    """Missing registry returns ``[]`` -- never raises. Caller wants a
    best-effort breadcrumb (the ``<hook_notice>`` body) and we must not
    break the hook on a clean machine that just installed."""
    registry = tmp_path / "does_not_exist.json"
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry))
    import importlib  # noqa: PLC0415

    import scripts.inject_memory_context as hook_module  # noqa: PLC0415

    importlib.reload(hook_module)
    assert hook_module._list_registry_entries() == []


def test_list_registry_entries_corrupt_file(tmp_path, monkeypatch) -> None:
    """Garbage JSON returns ``[]`` -- same best-effort contract."""
    registry = tmp_path / "workspaces.json"
    registry.write_text("not json {", encoding="utf-8")
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry))
    import importlib  # noqa: PLC0415

    import scripts.inject_memory_context as hook_module  # noqa: PLC0415

    importlib.reload(hook_module)
    assert hook_module._list_registry_entries() == []


def test_list_registry_entries_returns_workspaces(tmp_path, monkeypatch) -> None:
    """Well-formed registry returns the workspaces list verbatim, so the
    ``<hook_notice>`` body can list registered ids the operator could
    have routed to."""
    registry = tmp_path / "workspaces.json"
    registry.write_text(
        json.dumps({"workspaces": [{"id": "foo"}, {"id": "bar"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry))
    import importlib  # noqa: PLC0415

    import scripts.inject_memory_context as hook_module  # noqa: PLC0415

    importlib.reload(hook_module)
    entries = hook_module._list_registry_entries()
    assert sorted(e["id"] for e in entries) == ["bar", "foo"]


# ---------- registry walk-up resolver ----------


def test_resolve_from_registry_walks_up_to_registered_parent(tmp_path, monkeypatch) -> None:
    """``cwd`` deep inside a registered project resolves to that project's
    workspace_id even when the cwd itself isn't the project_root."""
    project_root = tmp_path / "project_alpha"
    nested_cwd = project_root / "src" / "deep"
    nested_cwd.mkdir(parents=True)
    registry = tmp_path / "workspaces.json"
    registry.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": "alpha",
                        "project_root": str(project_root),
                        "db_path": str(project_root / "memory.db"),
                        "vector_path": str(project_root / "vectors.lance"),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry))
    import importlib  # noqa: PLC0415

    import scripts.inject_memory_context as hook_module  # noqa: PLC0415

    importlib.reload(hook_module)
    result = hook_module._resolve_from_registry(nested_cwd)
    assert result is not None
    assert result["workspace_id"] == "alpha"


def test_resolve_from_registry_returns_none_when_unregistered(tmp_path, monkeypatch) -> None:
    """``cwd`` that is not inside any registered project_root yields
    ``None``; the main() flow then falls through to the global fallback
    branch and emits a <hook_notice> for the agent to see."""
    unrelated = tmp_path / "unrelated_dir"
    unrelated.mkdir()
    registry = tmp_path / "workspaces.json"
    registry.write_text(
        json.dumps({"workspaces": [{"id": "alpha", "project_root": str(tmp_path / "alpha")}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry))
    import importlib  # noqa: PLC0415

    import scripts.inject_memory_context as hook_module  # noqa: PLC0415

    importlib.reload(hook_module)
    assert hook_module._resolve_from_registry(unrelated) is None


# ---------- bug fixes from the empty-envelope investigation ----------


def test_hook_stdout_reconfigure_module_import_does_not_crash() -> None:
    """Importing the hook module triggers the UTF-8 stdout reconfigure
    block at module top. The reconfigure is wrapped in
    ``contextlib.suppress(AttributeError, ValueError)`` so test runners
    that wrap stdout (pytest capture) do not raise. Without the
    reconfigure, the production hook crashes with ``UnicodeEncodeError``
    on every envelope that contains an em-dash, arrow, or Cyrillic
    char -- exactly the content that ships in v1.10 + v2.2 envelopes."""
    import importlib  # noqa: PLC0415

    import scripts.inject_memory_context as hook_module  # noqa: PLC0415

    # Reload exercises the top-level reconfigure block again. If pytest's
    # captured stdout doesn't expose .reconfigure, the suppress catches
    # the AttributeError and the import completes cleanly.
    importlib.reload(hook_module)
    assert hook_module is not None


def test_hook_stdout_writes_unicode_arrow_without_crashing(tmp_path, monkeypatch) -> None:
    """Smoke: write the same unicode that crashed the production hook
    (em-dash, arrow, Cyrillic) to a UTF-8-reconfigured stream. The fix
    in the hook header is ``sys.stdout.reconfigure(encoding='utf-8',
    errors='replace')``; this test simulates the same call against a
    text-wrapped buffer and confirms the bytes round-trip."""
    raw = io.BytesIO()
    text_stream = io.TextIOWrapper(raw, encoding="cp1252", write_through=True)
    text_stream.reconfigure(encoding="utf-8", errors="replace")
    payload = "memory → capability — русский"
    text_stream.write(payload)
    text_stream.flush()
    assert raw.getvalue().decode("utf-8") == payload
