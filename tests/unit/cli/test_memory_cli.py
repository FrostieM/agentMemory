"""Unit tests for memory-cli — argparse wiring + envelope rendering.

Network calls are stubbed via monkeypatch on ``_request`` so the
tests run offline. The full HTTP path is covered by
``tests/e2e/test_v3_routes.py``.
"""

from __future__ import annotations

import importlib
import io
import json
from typing import Any

import pytest

from agent_memory_lite.cli import main as cli


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture the last _request call args + return a stub envelope."""
    captured: dict[str, Any] = {}

    def _fake_request(method, path, *, base_url, timeout, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["base_url"] = base_url
        captured["kwargs"] = kwargs
        return captured.get("response", {"ok": True, "data": {"echo": True}})

    monkeypatch.setattr(cli, "_request", _fake_request)
    return captured


def _run(argv: list[str]) -> int:
    return cli.main(argv)


def test_brief_calls_brief_endpoint(captured: dict, capsys: pytest.CaptureFixture) -> None:
    rc = _run(["brief", "--workspace", "ws-x"])
    assert rc == 0
    assert captured["path"] == "/memory/brief"
    assert captured["method"] == "GET"
    assert captured["kwargs"]["params"]["workspace_id"] == "ws-x"
    out = capsys.readouterr().out
    assert json.loads(out)["ok"] is True


def test_brief_text_mode_prints_body_md(captured: dict, capsys: pytest.CaptureFixture) -> None:
    captured["response"] = {
        "ok": True,
        "data": {"body_md": "# brief content", "token_count": 5},
    }
    rc = _run(["brief", "--workspace", "ws", "--text"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "# brief content" in out
    assert "token_count" not in out  # text mode strips envelope


@pytest.mark.parametrize("cmd", ["list", "count", "rollback", "versions"])
def test_removed_legacy_commands_are_not_registered(cmd: str) -> None:
    with pytest.raises(SystemExit) as exc:
        _run([cmd, "--workspace", "ws"])
    assert exc.value.code == 2


def test_get_with_fields(captured: dict) -> None:
    _run(
        [
            "get",
            "--workspace",
            "ws",
            "--kind",
            "decision",
            "--id",
            "dec_x",
            "--fields",
            "rationale,decision_text",
        ]
    )
    assert captured["kwargs"]["params"]["fields"] == "rationale,decision_text"


def test_search_csv_kinds(captured: dict) -> None:
    _run(
        [
            "search",
            "kelly",
            "--workspace",
            "ws",
            "--kinds",
            "decision,behavior",
            "--limit",
            "8",
        ]
    )
    body = captured["kwargs"]["json"]
    assert body["query"] == "kelly"
    assert body["kinds"] == ["decision", "behavior"]
    assert body["limit"] == 8


def test_write_inline_payload(captured: dict) -> None:
    _run(
        [
            "write",
            "--workspace",
            "ws",
            "--kind",
            "decision",
            "--payload",
            '{"title": "T", "decision_text": "body"}',
        ]
    )
    body = captured["kwargs"]["json"]
    assert body["payload"]["title"] == "T"
    assert body["agent_id"] == "memory-cli"


def test_write_stdin_payload(captured: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO('{"title": "from_stdin", "decision_text": "..."}'))
    _run(["write", "--workspace", "ws", "--kind", "decision", "--stdin"])
    body = captured["kwargs"]["json"]
    assert body["payload"]["title"] == "from_stdin"


def test_edit_partial_fields(captured: dict) -> None:
    _run(
        [
            "edit",
            "--workspace",
            "ws",
            "--kind",
            "decision",
            "--id",
            "dec_x",
            "--payload",
            '{"status": "superseded"}',
        ]
    )
    body = captured["kwargs"]["json"]
    assert body["fields"]["status"] == "superseded"


def test_pin_toggle(captured: dict) -> None:
    _run(["pin", "--workspace", "ws", "--kind", "decision", "--id", "dec_x"])
    assert captured["kwargs"]["json"]["pinned"] is True
    _run(["pin", "--workspace", "ws", "--kind", "decision", "--id", "dec_x", "--unpin"])
    assert captured["kwargs"]["json"]["pinned"] is False


def test_archive_with_reason(captured: dict) -> None:
    _run(
        [
            "archive",
            "--workspace",
            "ws",
            "--kind",
            "decision",
            "--id",
            "dec_x",
            "--reason",
            "obsolete after v3",
        ]
    )
    body = captured["kwargs"]["json"]
    assert body["reason"] == "obsolete after v3"


def test_lint_via_tool_name(captured: dict) -> None:
    _run(
        [
            "lint",
            "--workspace",
            "ws",
            "--tool-name",
            "Edit",
            "--payload",
            '{"file_path": "src/x.py"}',
        ]
    )
    body = captured["kwargs"]["json"]
    assert body["tool_name"] == "Edit"
    assert body["tool_payload"]["file_path"] == "src/x.py"


def test_skill_invoke(captured: dict) -> None:
    _run(["skill", "skill_x", "--workspace", "ws"])
    assert captured["path"] == "/memory/skill/skill_x"


def test_envelope_error_returns_nonzero(captured: dict, capsys: pytest.CaptureFixture) -> None:
    captured["response"] = {"ok": False, "error": {"code": "not_found", "message": "x"}}
    rc = _run(["get", "--workspace", "ws", "--kind", "decision", "--id", "dec_x"])
    assert rc == 1
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


def test_workspace_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AGENT_MEMORY_WORKSPACE is set, --workspace becomes optional.

    We rebuild the parser at run time so this is integration-style: just
    check that --workspace omission no longer fails parse.
    """
    monkeypatch.setenv("AGENT_MEMORY_WORKSPACE", "env-ws")
    # Re-import to refresh DEFAULT_WORKSPACE.
    importlib.reload(cli)
    parser = cli._build_parser()  # type: ignore[attr-defined]
    args = parser.parse_args(["get", "--kind", "decision", "--id", "dec_x"])
    assert args.workspace == "env-ws"
