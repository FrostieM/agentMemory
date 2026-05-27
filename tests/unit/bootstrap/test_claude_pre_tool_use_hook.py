"""Unit tests for the PreToolUse hook installer."""

from __future__ import annotations

from pathlib import Path

from agent_memory_lite.bootstrap.claude_pre_tool_use_hook import (
    HOOK_MARKER,
    HOOK_MATCHERS,
    install_pre_tool_use_hook,
)


def _vp() -> Path:
    return Path("/python")


def _hs() -> Path:
    return Path("/repo/scripts/pre_tool_use_check.py")


def test_fresh_install_creates_pretooluse_block() -> None:
    settings: dict = {}
    status = install_pre_tool_use_hook(settings, venv_python=_vp(), hook_script=_hs())
    assert status == "installed"
    blocks = settings["hooks"]["PreToolUse"]
    assert [block["matcher"] for block in blocks] == list(HOOK_MATCHERS)
    assert all(HOOK_MARKER in block["hooks"][0]["command"] for block in blocks)


def test_second_install_is_unchanged() -> None:
    settings: dict = {}
    install_pre_tool_use_hook(settings, venv_python=_vp(), hook_script=_hs())
    status = install_pre_tool_use_hook(settings, venv_python=_vp(), hook_script=_hs())
    assert status == "unchanged"
    assert len(settings["hooks"]["PreToolUse"]) == len(HOOK_MATCHERS)


def test_install_refreshes_when_command_changes() -> None:
    settings: dict = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Edit",
                    "hooks": [{"type": "command", "command": f"old-cmd # {HOOK_MARKER}"}],
                }
            ]
        }
    }
    status = install_pre_tool_use_hook(settings, venv_python=_vp(), hook_script=_hs())
    assert status == "refreshed"
    blocks = settings["hooks"]["PreToolUse"]
    assert [block["matcher"] for block in blocks] == list(HOOK_MATCHERS)
    assert all("old-cmd" not in block["hooks"][0]["command"] for block in blocks)


def test_install_preserves_unrelated_hooks() -> None:
    settings: dict = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Read",
                    "hooks": [{"type": "command", "command": "other-tool"}],
                }
            ]
        }
    }
    install_pre_tool_use_hook(settings, venv_python=_vp(), hook_script=_hs())
    blocks = settings["hooks"]["PreToolUse"]
    assert len(blocks) == len(HOOK_MATCHERS) + 1
    assert any(b["matcher"] == "Read" for b in blocks)
    assert all(any(b["matcher"] == matcher for b in blocks) for matcher in HOOK_MATCHERS)


def test_install_repairs_malformed_hooks_subtree() -> None:
    settings: dict = {"hooks": "not a dict"}  # corrupt prior file
    status = install_pre_tool_use_hook(settings, venv_python=_vp(), hook_script=_hs())
    assert status == "installed"
    assert isinstance(settings["hooks"], dict)
    assert [block["matcher"] for block in settings["hooks"]["PreToolUse"]] == list(HOOK_MATCHERS)


def test_install_repairs_malformed_pretooluse_subtree() -> None:
    settings: dict = {"hooks": {"PreToolUse": {"not": "a list"}}}
    status = install_pre_tool_use_hook(settings, venv_python=_vp(), hook_script=_hs())
    assert status == "installed"
    assert isinstance(settings["hooks"]["PreToolUse"], list)
