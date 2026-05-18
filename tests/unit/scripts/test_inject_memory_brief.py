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

    # Stub _fetch_brief to confirm the workspace_id passed in is "global".
    captured: dict[str, Any] = {}

    def fake_fetch(
        *, base_url: str, workspace_id: str, max_tokens: int, headers: Any
    ) -> dict[str, Any] | None:
        captured["workspace_id"] = workspace_id
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
    assert "<memory_brief>" not in out
