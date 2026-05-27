"""Unit tests for scripts/inject_memory_brief.py.

Covers:

* Workspace resolution via the registry walker.
* /memory/brief call success path (envelope unwrap → emit body_md).
* Failure modes: HTTP error, non-JSON, ok=false, empty body_md.
* Emission shape — ``<agent-memory><memory_brief>...``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripts import inject_memory_brief as v3hook

# ============================================================
# Workspace resolution
# ============================================================


def _write_registry(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({"version": 1, "workspaces": entries}), encoding="utf-8")


def test_env_first_prefers_canonical_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_WORKSPACE_ID", "canonical-ws")
    monkeypatch.setenv("AGENT_MEMORY_WORKSPACE", "legacy-ws")
    assert v3hook._env_first("MEMORY_WORKSPACE_ID", "AGENT_MEMORY_WORKSPACE") == "canonical-ws"


def test_resolve_finds_workspace_by_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "workspaces.json"
    project = tmp_path / "agent-memory-lite"
    project.mkdir()
    _write_registry(
        registry,
        [
            {
                "id": "agent-memory-lite",
                "project_root": str(project),
                "db_path": str(tmp_path / "aml.db"),
                "vector_path": str(tmp_path / "aml.lance"),
            }
        ],
    )
    monkeypatch.setattr(v3hook, "REGISTRY_PATH", registry)
    ws, db, vec = v3hook._resolve_workspace_from_cwd(project)
    assert ws == "agent-memory-lite"
    assert db.endswith("aml.db")
    assert vec.endswith("aml.lance")


def test_resolve_walks_parents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "workspaces.json"
    project = tmp_path / "proj"
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)
    _write_registry(
        registry,
        [
            {
                "id": "proj",
                "project_root": str(project),
                "db_path": "x.db",
                "vector_path": "x.lance",
            }
        ],
    )
    monkeypatch.setattr(v3hook, "REGISTRY_PATH", registry)
    ws, _db, _vec = v3hook._resolve_workspace_from_cwd(nested)
    assert ws == "proj"


def test_resolve_returns_empty_when_no_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "workspaces.json"
    _write_registry(registry, [])
    monkeypatch.setattr(v3hook, "REGISTRY_PATH", registry)
    ws, db, vec = v3hook._resolve_workspace_from_cwd(tmp_path / "elsewhere")
    assert ws == ""
    assert db == ""
    assert vec == ""


def test_resolve_tolerates_missing_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(v3hook, "REGISTRY_PATH", tmp_path / "nope.json")
    ws, db, vec = v3hook._resolve_workspace_from_cwd(tmp_path)
    assert ws == ""
    assert db == ""
    assert vec == ""


# ============================================================
# /memory/brief fetch
# ============================================================


def _mock_transport(
    *, status_code: int = 200, json_body: dict[str, Any] | None = None
) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        body = json_body if json_body is not None else {"ok": True, "data": {}}
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


def test_fetch_brief_returns_data_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _mock_transport(
        json_body={
            "ok": True,
            "data": {"body_md": "# brief\nidentity\nbehaviors", "token_count": 42},
        }
    )

    def fake_get(url: str, *, params: Any, headers: Any, timeout: Any) -> httpx.Response:
        client = httpx.Client(transport=transport)
        return client.get(url, params=params, headers=headers, timeout=timeout)

    monkeypatch.setattr(v3hook.httpx, "get", fake_get)
    data = v3hook._fetch_brief(
        base_url="http://x",
        workspace_id="ws",
        max_tokens=500,
        headers={},
    )
    assert data is not None
    assert data["body_md"].startswith("# brief")


def test_fetch_brief_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_kw: Any) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(v3hook.httpx, "get", boom)
    assert (
        v3hook._fetch_brief(base_url="http://x", workspace_id="ws", max_tokens=500, headers={})
        is None
    )


def test_fetch_brief_returns_none_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _mock_transport(status_code=500, json_body={"ok": False})

    def fake_get(url: str, *, params: Any, headers: Any, timeout: Any) -> httpx.Response:
        client = httpx.Client(transport=transport)
        return client.get(url, params=params, headers=headers, timeout=timeout)

    monkeypatch.setattr(v3hook.httpx, "get", fake_get)
    assert (
        v3hook._fetch_brief(base_url="http://x", workspace_id="ws", max_tokens=500, headers={})
        is None
    )


def test_fetch_brief_returns_none_on_envelope_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _mock_transport(json_body={"ok": False, "error": {"code": "x", "message": "y"}})

    def fake_get(url: str, *, params: Any, headers: Any, timeout: Any) -> httpx.Response:
        client = httpx.Client(transport=transport)
        return client.get(url, params=params, headers=headers, timeout=timeout)

    monkeypatch.setattr(v3hook.httpx, "get", fake_get)
    assert (
        v3hook._fetch_brief(base_url="http://x", workspace_id="ws", max_tokens=500, headers={})
        is None
    )


# ============================================================
# Emission
# ============================================================


def test_emit_brief_wraps_in_agent_memory_block(capsys: pytest.CaptureFixture) -> None:
    v3hook._emit_brief("# Identity\nProject: agent-memory-lite")
    out = capsys.readouterr().out
    assert out.startswith("<agent-memory>\n<memory_brief>\n")
    assert out.rstrip().endswith("</memory_brief>\n</agent-memory>".rstrip())
    assert "# Identity" in out


def test_emit_notice_format(capsys: pytest.CaptureFixture) -> None:
    v3hook._emit_notice("service down")
    out = capsys.readouterr().out
    assert "<!-- memory brief hook notice: service down -->" in out


# ============================================================
# Global-fallback behaviour
# ============================================================


def test_main_falls_back_to_global_when_cwd_unregistered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """When cwd matches no project_root, brief must fall back to `global`."""
    registry = tmp_path / "workspaces.json"
    _write_registry(
        registry,
        [
            {
                "id": "global",
                "project_root": str(tmp_path / "global-dir"),
                "db_path": str(tmp_path / "global.db"),
                "vector_path": "",
            }
        ],
    )
    monkeypatch.setattr(v3hook, "REGISTRY_PATH", registry)
    monkeypatch.setattr(v3hook, "HOOK_FALLBACK_DISABLED", False)
    monkeypatch.setattr(v3hook, "DEFAULT_WORKSPACE", "")

    # Stub _fetch_brief to confirm the workspace_id passed in is "global".
    captured: dict[str, Any] = {}

    def fake_fetch(
        *,
        base_url: str,
        workspace_id: str,
        max_tokens: int,
        headers: Any,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        captured["workspace_id"] = workspace_id
        captured["session_id"] = session_id
        return {"body_md": "# fallback body\nrules ...", "token_count": 8}

    monkeypatch.setattr(v3hook, "_fetch_brief", fake_fetch)

    import io  # noqa: PLC0415

    monkeypatch.setattr(
        "sys.stdin", io.StringIO('{"prompt": "anything", "cwd": "/some/unregistered/path"}')
    )
    monkeypatch.setattr("os.getcwd", lambda: "/some/unregistered/path")

    rc = v3hook.main()
    assert rc == 0
    assert captured.get("workspace_id") == "global"
    out = capsys.readouterr().out
    # Brief should appear with the hook_notice prefix.
    assert "global_fallback" in out
    assert "<memory_brief>" in out
    assert "# fallback body" in out


def test_main_global_fallback_uses_default_paths_when_registry_lacks_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    registry = tmp_path / "workspaces.json"
    _write_registry(registry, [])
    global_root = tmp_path / "global-root"
    captured: dict[str, Any] = {}
    monkeypatch.setattr(v3hook, "REGISTRY_PATH", registry)
    monkeypatch.setattr(v3hook, "GLOBAL_FALLBACK_ROOT", global_root)
    monkeypatch.setattr(v3hook, "HOOK_FALLBACK_DISABLED", False)
    monkeypatch.setattr(v3hook, "DEFAULT_WORKSPACE", "")
    monkeypatch.setattr(v3hook, "DEFAULT_DEDUP_ENABLED", False)
    calls: dict[str, Any] = {}

    def fake_bootstrap(db_path: str, *, workspace_id: str) -> None:
        calls["bootstrap"] = {"db_path": db_path, "workspace_id": workspace_id}

    monkeypatch.setattr(v3hook, "_bootstrap_global_workspace", fake_bootstrap)

    def fake_fetch(
        *,
        base_url: str,
        workspace_id: str,
        max_tokens: int,
        headers: Any,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        del base_url, max_tokens, session_id
        captured["workspace_id"] = workspace_id
        captured["headers"] = headers
        return {"body_md": "# fallback body"}

    monkeypatch.setattr(v3hook, "_fetch_brief", fake_fetch)

    import io  # noqa: PLC0415

    monkeypatch.setattr(
        "sys.stdin", io.StringIO('{"prompt": "anything", "cwd": "/some/unregistered/path"}')
    )
    monkeypatch.setattr("os.getcwd", lambda: "/some/unregistered/path")

    assert v3hook.main() == 0
    capsys.readouterr()
    assert captured["workspace_id"] == "global"
    assert captured["headers"]["X-Memory-DB-Path"] == str(global_root / "memory.db")
    assert captured["headers"]["X-Memory-Vector-Path"] == str(global_root / "vectors.lance")
    assert calls["bootstrap"] == {
        "db_path": str(global_root / "memory.db"),
        "workspace_id": "global",
    }


def test_main_uses_canonical_env_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    captured: dict[str, Any] = {}
    db_path = tmp_path / "env.db"
    vector_path = tmp_path / "env.lance"
    monkeypatch.setenv("MEMORY_DB_PATH", str(db_path))
    monkeypatch.setenv("VECTOR_DB_PATH", str(vector_path))
    monkeypatch.setattr(v3hook, "DEFAULT_WORKSPACE", "env-ws")
    monkeypatch.setattr(v3hook, "DEFAULT_DEDUP_ENABLED", False)

    def fake_fetch(
        *,
        base_url: str,
        workspace_id: str,
        max_tokens: int,
        headers: Any,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        del base_url, max_tokens, session_id
        captured["workspace_id"] = workspace_id
        captured["headers"] = headers
        return {"body_md": "# env body"}

    monkeypatch.setattr(v3hook, "_fetch_brief", fake_fetch)

    import io  # noqa: PLC0415

    monkeypatch.setattr("sys.stdin", io.StringIO('{"prompt": "x"}'))
    assert v3hook.main() == 0
    capsys.readouterr()
    assert captured["workspace_id"] == "env-ws"
    assert captured["headers"]["X-Memory-DB-Path"] == str(db_path)
    assert captured["headers"]["X-Memory-Vector-Path"] == str(vector_path)


def test_main_rejects_relative_env_db_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("MEMORY_DB_PATH", "relative.db")
    monkeypatch.setattr(v3hook, "REGISTRY_PATH", tmp_path / "missing-workspaces.json")
    monkeypatch.setattr(v3hook, "DEFAULT_WORKSPACE", "env-ws")
    monkeypatch.setattr(v3hook, "DEFAULT_DEDUP_ENABLED", False)

    def fake_fetch(
        *,
        base_url: str,
        workspace_id: str,
        max_tokens: int,
        headers: Any,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        del base_url, workspace_id, max_tokens, session_id
        captured["headers"] = headers
        return {"body_md": "# env body"}

    monkeypatch.setattr(v3hook, "_fetch_brief", fake_fetch)

    import io  # noqa: PLC0415

    monkeypatch.setattr("sys.stdin", io.StringIO('{"prompt": "x"}'))
    assert v3hook.main() == 0
    capsys.readouterr()
    assert "X-Memory-DB-Path" not in captured["headers"]


def test_main_respects_hook_fallback_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """When AGENT_MEMORY_HOOK_FALLBACK=disabled, no global fallback — old notice."""
    registry = tmp_path / "workspaces.json"
    _write_registry(registry, [])
    monkeypatch.setattr(v3hook, "REGISTRY_PATH", registry)
    monkeypatch.setattr(v3hook, "HOOK_FALLBACK_DISABLED", True)

    import io  # noqa: PLC0415

    monkeypatch.setattr("sys.stdin", io.StringIO('{"prompt": "x", "cwd": "/nowhere"}'))
    monkeypatch.setattr("os.getcwd", lambda: "/nowhere")

    rc = v3hook.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "no workspace registered" in out


# ============================================================
# v3.2 once-per-session dedup
# ============================================================


def test_should_skip_brief_first_call_emits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First call for a fresh session always emits the full brief."""
    monkeypatch.setattr(v3hook, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(v3hook, "DEFAULT_DEDUP_ENABLED", True)
    skip, turn = v3hook._should_skip_brief(session_id="sess_new", body_md="brief content v1")
    assert skip is False
    assert turn == 1


def test_should_skip_brief_unchanged_body_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identical body on a subsequent call → skip."""
    monkeypatch.setattr(v3hook, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(v3hook, "DEFAULT_DEDUP_ENABLED", True)
    monkeypatch.setattr(v3hook, "DEFAULT_REFRESH_EVERY_N", 20)
    body = "brief content v1\nwith multiple lines"
    skip1, t1 = v3hook._should_skip_brief(session_id="sess_x", body_md=body)
    skip2, t2 = v3hook._should_skip_brief(session_id="sess_x", body_md=body)
    skip3, t3 = v3hook._should_skip_brief(session_id="sess_x", body_md=body)
    assert (skip1, t1) == (False, 1)
    assert (skip2, t2) == (True, 2)
    assert (skip3, t3) == (True, 3)


def test_should_skip_brief_changed_body_emits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When body_md changes (memory state moved), emit fresh brief
    and reset the turn counter."""
    monkeypatch.setattr(v3hook, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(v3hook, "DEFAULT_DEDUP_ENABLED", True)
    skip_a, turn_a = v3hook._should_skip_brief(session_id="sess_y", body_md="version A")
    skip_b, turn_b = v3hook._should_skip_brief(session_id="sess_y", body_md="version B")
    assert (skip_a, turn_a) == (False, 1)
    assert (skip_b, turn_b) == (False, 1)


def test_should_skip_brief_refresh_every_n_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Periodic refresh: after N identical-body calls, the hook re-emits."""
    monkeypatch.setattr(v3hook, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(v3hook, "DEFAULT_DEDUP_ENABLED", True)
    monkeypatch.setattr(v3hook, "DEFAULT_REFRESH_EVERY_N", 3)
    body = "stable brief"
    out = [v3hook._should_skip_brief(session_id="sess_z", body_md=body) for _ in range(5)]
    # turn=1 emit; turn=2 skip; turn=3 emit (refresh boundary, counter resets);
    # then 4=>1 emit, 5=>2 skip again.
    skips = [s for s, _ in out]
    assert skips == [False, True, False, True, False]


def test_should_skip_brief_disabled_never_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator escape hatch: MEMORY_BRIEF_DEDUP_ENABLED=false → always emit."""
    monkeypatch.setattr(v3hook, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(v3hook, "DEFAULT_DEDUP_ENABLED", False)
    body = "stable brief"
    skip1, _ = v3hook._should_skip_brief(session_id="sess_w", body_md=body)
    skip2, _ = v3hook._should_skip_brief(session_id="sess_w", body_md=body)
    assert skip1 is False
    assert skip2 is False


def test_should_skip_brief_no_session_id_never_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No stable session_id → dedup is impossible; emit every time."""
    monkeypatch.setattr(v3hook, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(v3hook, "DEFAULT_DEDUP_ENABLED", True)
    skip_a, _ = v3hook._should_skip_brief(session_id=None, body_md="content")
    skip_b, _ = v3hook._should_skip_brief(session_id="", body_md="content")
    assert skip_a is False
    assert skip_b is False


def test_session_memo_path_is_filesystem_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pathological session_id (with slashes / Windows-illegal chars)
    must produce a usable filename."""
    monkeypatch.setattr(v3hook, "SESSIONS_DIR", tmp_path)
    p = v3hook._session_memo_path("C:/Users/x/transcript_2026-05-20.jsonl")
    # The path should be safe: no path separators in the basename.
    assert "/" not in p.name
    assert "\\" not in p.name
    assert p.name.startswith("brief_")


def test_emit_unchanged_format(capsys: pytest.CaptureFixture) -> None:
    """The 1-line skip notice carries the turn counter and stays short."""
    v3hook._emit_unchanged(7)
    out = capsys.readouterr().out
    assert "<agent-memory>" in out
    assert "memory brief unchanged" in out
    assert "turn=7" in out
    # Should be substantially shorter than a real brief.
    assert len(out) < 400


def test_main_dedup_emits_unchanged_on_second_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """End-to-end: same session_id + same brief body → 2nd call emits
    the 1-line unchanged notice instead of the full brief."""
    registry = tmp_path / "workspaces.json"
    project = tmp_path / "proj"
    project.mkdir()
    _write_registry(
        registry,
        [
            {
                "id": "proj",
                "project_root": str(project),
                "db_path": "x.db",
                "vector_path": "x.lance",
            }
        ],
    )
    monkeypatch.setattr(v3hook, "REGISTRY_PATH", registry)
    monkeypatch.setattr(v3hook, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(v3hook, "DEFAULT_DEDUP_ENABLED", True)
    monkeypatch.setattr(v3hook, "DEFAULT_REFRESH_EVERY_N", 20)

    stable_body = "# Identity\n- pinned discipline rule one\n- pinned discipline rule two\n"
    monkeypatch.setattr(
        v3hook,
        "_fetch_brief",
        lambda **_: {"body_md": stable_body},
    )

    import io  # noqa: PLC0415

    event_json = json.dumps(
        {
            "prompt": "what's the status",
            "cwd": str(project),
            "session_id": "sess_e2e_dedup",
        }
    )

    # First turn: full brief.
    monkeypatch.setattr("sys.stdin", io.StringIO(event_json))
    monkeypatch.setattr("os.getcwd", lambda: str(project))
    assert v3hook.main() == 0
    first = capsys.readouterr().out
    assert "<memory_brief>" in first
    assert "pinned discipline rule one" in first

    # Second turn (same session, same body): unchanged notice only.
    monkeypatch.setattr("sys.stdin", io.StringIO(event_json))
    monkeypatch.setattr("os.getcwd", lambda: str(project))
    assert v3hook.main() == 0
    second_out = capsys.readouterr().out
    assert "<memory_brief>" not in second_out
    assert "memory brief unchanged" in second_out
    assert "turn=2" in second_out
