"""Install the Claude Code PreToolUse enforcement hook.

The hook command points at ``scripts/pre_tool_use_check.py`` from this
repo; the hook script reads the active workspace from cwd → registry
and routes tool calls through the enforcement-layer dispatcher.

The matcher is intentionally narrow: only Edit / Write / NotebookEdit /
Bash and the agent-memory-lite memory write tools. Wider matchers
(e.g. ``"*"``) would pay subprocess startup cost on every Read / Grep
which never trips an enforcement rule.

Idempotent: re-running ``setup_agent`` refreshes the existing hook
entry instead of duplicating it. Identification is by a marker string
embedded in the hook command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HOOK_MARKER = "agent-memory-lite-pretooluse"
HOOK_MATCHER = "Edit|Write|NotebookEdit|Bash|mcp__agent-memory-lite__memory_.*"


def _hook_command(*, venv_python: Path, hook_script: Path) -> str:
    return f'"{venv_python}" "{hook_script}" # {HOOK_MARKER}'


def _find_existing(
    pretooluse: list[Any],
) -> dict[str, Any] | None:
    for entry in pretooluse:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        if any(isinstance(h, dict) and HOOK_MARKER in str(h.get("command", "")) for h in hooks):
            return entry
    return None


def install_pre_tool_use_hook(
    settings: dict[str, Any],
    *,
    venv_python: Path,
    hook_script: Path,
) -> str:
    """Mutate ``settings`` in place; return one of installed / refreshed / unchanged.

    ``settings`` is the parsed ~/.claude/settings.json (or a
    per-project settings.json) dict. The caller is responsible for
    writing it back to disk.
    """
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    pretooluse = hooks.setdefault("PreToolUse", [])
    if not isinstance(pretooluse, list):
        pretooluse = []
        hooks["PreToolUse"] = pretooluse

    new_hook = {
        "type": "command",
        "command": _hook_command(venv_python=venv_python, hook_script=hook_script),
    }
    new_entry = {"matcher": HOOK_MATCHER, "hooks": [new_hook]}

    existing = _find_existing(pretooluse)
    if existing is None:
        pretooluse.append(new_entry)
        return "installed"

    existing_hooks = existing.get("hooks", [])
    if (
        isinstance(existing_hooks, list)
        and len(existing_hooks) == 1
        and isinstance(existing_hooks[0], dict)
        and existing_hooks[0].get("command") == new_hook["command"]
        and existing.get("matcher") == HOOK_MATCHER
    ):
        return "unchanged"

    existing["matcher"] = HOOK_MATCHER
    existing["hooks"] = [new_hook]
    return "refreshed"
