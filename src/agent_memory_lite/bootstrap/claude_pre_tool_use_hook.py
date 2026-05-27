"""Install the Claude Code PreToolUse enforcement hook.

The hook command points at ``scripts/pre_tool_use_check.py`` from this
repo; the hook script reads the active workspace from cwd → registry
and routes tool calls through the enforcement-layer dispatcher.

The matcher is intentionally narrow: only source-read/mutate tools,
Bash and the agent-memory-lite memory tools. Wider matchers (e.g.
``"*"``) would pay subprocess startup cost on unrelated tool calls.

Idempotent: re-running ``setup_agent`` refreshes the existing hook
entry instead of duplicating it. Identification is by a marker string
embedded in the hook command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HOOK_MARKER = "agent-memory-lite-pretooluse"
HOOK_MATCHERS = (
    "Read",
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "Grep",
    "Bash",
    "mcp__agent-memory-lite__memory_.*",
)
HOOK_MATCHER = "|".join(HOOK_MATCHERS)


def _hook_command(*, venv_python: Path, hook_script: Path) -> str:
    return f'"{venv_python}" "{hook_script}" # {HOOK_MARKER}'


def _is_marked_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(isinstance(h, dict) and HOOK_MARKER in str(h.get("command", "")) for h in hooks)


def _find_existing(pretooluse: list[Any]) -> list[dict[str, Any]]:
    existing: list[dict[str, Any]] = []
    for entry in pretooluse:
        if _is_marked_entry(entry):
            existing.append(entry)
    return existing


def _entry_matches(entry: dict[str, Any], *, matcher: str, command: str) -> bool:
    hooks = entry.get("hooks", [])
    return (
        entry.get("matcher") == matcher
        and isinstance(hooks, list)
        and len(hooks) == 1
        and isinstance(hooks[0], dict)
        and hooks[0].get("command") == command
    )


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
    new_entries = [{"matcher": matcher, "hooks": [dict(new_hook)]} for matcher in HOOK_MATCHERS]

    existing = _find_existing(pretooluse)
    if not existing:
        pretooluse.extend(new_entries)
        return "installed"

    if len(existing) == len(new_entries) and all(
        _entry_matches(entry, matcher=matcher, command=new_hook["command"])
        for entry, matcher in zip(existing, HOOK_MATCHERS, strict=False)
    ):
        return "unchanged"

    pretooluse[:] = [entry for entry in pretooluse if not _is_marked_entry(entry)]
    pretooluse.extend(new_entries)
    return "refreshed"
