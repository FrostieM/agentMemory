"""Install the memory hook + seed stack for one project. Operator-facing.

This is the deployment surface for Phase 5 of the plan.  It wires
the four discipline layers (impact_check primitive, lint advisory,
brief identity line, pinned seed rules) into a project's actual
Claude Code installation:

  Active hook set: ``UserPromptSubmit`` + ``PreToolUse`` + ``PostToolUse``.

  1. Project-scoped ``.claude/settings.json`` gains a
     ``UserPromptSubmit`` hook → ``inject_memory_brief.py`` and a
     ``PostToolUse`` hook → ``post_edit_enqueue.py``.
  2. The workspace's SQLite DB has root migrations applied (if not already)
     and the pinned discipline behaviors seeded.

Dry-run by default — emits the settings.json delta and seed plan
to stdout without touching disk.  Pass ``--apply`` to write.  Pass
``--backup-first`` to copy any existing settings.json next to it
before modification.

Idempotent:  re-running with ``--apply`` is a no-op for hooks
already present and rules already seeded.

Usage::

    # Preview the changes for one project (no writes):
    python scripts/install_memory_hooks.py --project C:\\path\\to\\repo

    # Apply for real:
    python scripts/install_memory_hooks.py --project C:\\path\\to\\repo --apply

    # Hooks only, skip seed step:
    python scripts/install_memory_hooks.py --project C:\\path\\to\\repo --apply --no-seed

    # Seed only, skip hook wiring:
    python scripts/install_memory_hooks.py --project C:\\path\\to\\repo --apply --no-hooks
"""

from __future__ import annotations

# ruff: noqa: I001

import argparse
import contextlib
import json
import shutil
import sqlite3
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Make sibling scripts importable when this file is run directly. Tests use
# pytest's auto-rootdir, but `python scripts/install_memory_hooks.py` runs with
# only ``scripts/`` on sys.path, which means ``from scripts.seed_memory_discipline``
# fails. Prepend the repo root so both run styles work.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
for _path in (_SRC_ROOT, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


# Force UTF-8 stdout — the human render has → / em-dash / non-ASCII
# glyphs that crash a default Windows cp1251 console.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
with contextlib.suppress(AttributeError, ValueError):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIEF_HOOK_SCRIPT = REPO_ROOT / "scripts" / "inject_memory_brief.py"
POSTEDIT_HOOK_SCRIPT = REPO_ROOT / "scripts" / "post_edit_enqueue.py"
PRETOOLUSE_HOOK_SCRIPT = REPO_ROOT / "scripts" / "pre_tool_use_check.py"
SEED_SCRIPT = REPO_ROOT / "scripts" / "seed_memory_discipline.py"
POSTTOOLUSE_MATCHER = "Edit|Write|NotebookEdit|MultiEdit"
PRETOOLUSE_HOOK_MARKER = "agent-memory-lite-pretooluse"
PRETOOLUSE_MATCHER = (
    "Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Bash|mcp__agent-memory-lite__memory_.*"
)


# ============================================================
# Plan + report types
# ============================================================


@dataclass(slots=True)
class HookChange:
    """One pending modification to .claude/settings.json."""

    event: str  # "UserPromptSubmit" | "PostToolUse"
    matcher: str | None
    command: str
    status: str = "pending"  # "pending" | "applied" | "skipped"


@dataclass(slots=True)
class InstallPlan:
    """Full plan for one project installation."""

    project_root: Path
    settings_path: Path
    hooks: list[HookChange] = field(default_factory=list)
    db_path: Path | None = None
    seed_rules: list[str] = field(default_factory=list)
    workspace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": str(self.project_root),
            "settings_path": str(self.settings_path),
            "workspace_id": self.workspace_id,
            "db_path": str(self.db_path) if self.db_path else None,
            "hooks": [
                {
                    "event": h.event,
                    "matcher": h.matcher,
                    "command": h.command,
                    "status": h.status,
                }
                for h in self.hooks
            ],
            "seed_rules": list(self.seed_rules),
        }


# ============================================================
# Settings.json manipulation
# ============================================================


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _iter_hook_entries(
    settings: dict[str, Any], event: str
) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    hooks_root = settings.get("hooks")
    if not isinstance(hooks_root, dict):
        return
    entries = hooks_root.get(event)
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hook_list = entry.get("hooks", [])
        if not isinstance(hook_list, list):
            continue
        for inner in hook_list:
            if isinstance(inner, dict):
                yield entry, inner


def _hook_already_installed(
    settings: dict[str, Any],
    event: str,
    command_substr: str,
    *,
    matcher: str | None = None,
) -> bool:
    """True if hook entries under ``event`` already cover ``matcher``.

    ``setup_agent.py`` writes one ``PreToolUse`` entry per matcher token while
    this installer stores a compact pipe-separated matcher. Treat both forms as
    equivalent so the two operator paths stay idempotent with each other.
    """
    required_tokens = _matcher_tokens(matcher)
    installed_tokens: set[str] = set()
    for entry, inner in _iter_hook_entries(settings, event):
        cmd = str(inner.get("command", ""))
        if command_substr not in cmd:
            continue
        if matcher is None:
            return True
        raw_matcher = entry.get("matcher")
        if raw_matcher is None:
            return True
        if raw_matcher == matcher:
            return True
        installed_tokens.update(_matcher_tokens(str(raw_matcher)))
    return bool(required_tokens) and required_tokens <= installed_tokens


def _matcher_tokens(matcher: str | None) -> set[str]:
    if matcher is None:
        return set()
    return {token.strip() for token in matcher.split("|") if token.strip()}


def _remove_matching_hook_entries(settings: dict[str, Any], event: str, command_substr: str) -> int:
    """Remove existing entries for one installed script before refresh."""
    hooks_root = settings.get("hooks")
    if not isinstance(hooks_root, dict):
        return 0
    entries = hooks_root.get(event)
    if not isinstance(entries, list):
        return 0
    removed = 0
    kept_entries: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            kept_entries.append(entry)
            continue
        hook_list = entry.get("hooks", [])
        if not isinstance(hook_list, list):
            kept_entries.append(entry)
            continue
        kept_hooks: list[Any] = []
        for hook in hook_list:
            if isinstance(hook, dict) and command_substr in str(hook.get("command", "")):
                removed += 1
                continue
            kept_hooks.append(hook)
        if kept_hooks:
            entry["hooks"] = kept_hooks
            kept_entries.append(entry)
    hooks_root[event] = kept_entries
    return removed


def _add_hook(
    settings: dict[str, Any],
    *,
    event: str,
    matcher: str | None,
    command: str,
) -> None:
    """Append a new hook entry to settings under ``event``."""
    hooks_root = settings.setdefault("hooks", {})
    if not isinstance(hooks_root, dict):
        settings["hooks"] = {}
        hooks_root = settings["hooks"]
    entries = hooks_root.setdefault(event, [])
    if not isinstance(entries, list):
        hooks_root[event] = []
        entries = hooks_root[event]
    new_entry: dict[str, Any] = {
        "hooks": [{"type": "command", "command": command}],
    }
    if matcher is not None:
        new_entry["matcher"] = matcher
    entries.append(new_entry)


def _remove_legacy_inject_hooks(settings: dict[str, Any]) -> int:
    """Remove old inject_memory_context.py commands from UserPromptSubmit."""
    hooks_root = settings.get("hooks")
    if not isinstance(hooks_root, dict):
        return 0
    entries = hooks_root.get("UserPromptSubmit")
    if not isinstance(entries, list):
        return 0
    removed = 0
    kept_entries: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            kept_entries.append(entry)
            continue
        hook_list = entry.get("hooks", [])
        if not isinstance(hook_list, list):
            kept_entries.append(entry)
            continue
        kept_hooks: list[Any] = []
        for hook in hook_list:
            if not isinstance(hook, dict):
                kept_hooks.append(hook)
                continue
            command = str(hook.get("command", ""))
            if "agent-memory-lite-inject" in command or "inject_memory_context.py" in command:
                removed += 1
                continue
            kept_hooks.append(hook)
        if kept_hooks:
            entry["hooks"] = kept_hooks
            kept_entries.append(entry)
    hooks_root["UserPromptSubmit"] = kept_entries
    return removed


def _python_command(python_bin: str, script_path: Path) -> str:
    """Build the shell command Claude Code will exec for a hook."""
    return f'"{python_bin}" "{script_path}"'


# ============================================================
# Plan builder
# ============================================================


def build_plan(
    *,
    project_root: Path,
    python_bin: str,
    install_hooks: bool,
    install_seed: bool,
    workspaces_file: Path | None = None,
) -> InstallPlan:
    """Compute everything that would change. Pure function — no disk writes."""
    settings_path = project_root / ".claude" / "settings.json"
    plan = InstallPlan(project_root=project_root, settings_path=settings_path)

    # Resolve the workspace via the registry — must already be registered.
    if workspaces_file is None:
        workspaces_file = Path.home() / ".agent_memory" / "workspaces.json"
    try:
        from agent_memory_lite.config.workspace_registry import WorkspaceRegistry  # noqa: PLC0415

        registry = WorkspaceRegistry(workspaces_file)
        entries = registry.list()
    except Exception:
        entries = []
    target_root = str(project_root.resolve()).rstrip("\\/").casefold()
    matched = next(
        (e for e in entries if str(e.project_root or "").rstrip("\\/").casefold() == target_root),
        None,
    )
    if matched is not None:
        plan.workspace_id = matched.id
        plan.db_path = Path(matched.db_path) if matched.db_path else None

    if install_hooks:
        current = _load_settings(settings_path)
        brief_cmd = _python_command(python_bin, BRIEF_HOOK_SCRIPT)
        postedit_cmd = _python_command(python_bin, POSTEDIT_HOOK_SCRIPT)
        pretooluse_cmd = (
            _python_command(python_bin, PRETOOLUSE_HOOK_SCRIPT) + f" # {PRETOOLUSE_HOOK_MARKER}"
        )
        plan.hooks.append(
            HookChange(
                event="UserPromptSubmit",
                matcher=None,
                command=brief_cmd,
                status=(
                    "skipped"
                    if _hook_already_installed(current, "UserPromptSubmit", "inject_memory_brief")
                    else "pending"
                ),
            )
        )
        plan.hooks.append(
            HookChange(
                event="PreToolUse",
                matcher=PRETOOLUSE_MATCHER,
                command=pretooluse_cmd,
                status=(
                    "skipped"
                    if _hook_already_installed(
                        current,
                        "PreToolUse",
                        "pre_tool_use_check",
                        matcher=PRETOOLUSE_MATCHER,
                    )
                    else "pending"
                ),
            )
        )
        plan.hooks.append(
            HookChange(
                event="PostToolUse",
                matcher=POSTTOOLUSE_MATCHER,
                command=postedit_cmd,
                status=(
                    "skipped"
                    if _hook_already_installed(current, "PostToolUse", "post_edit_enqueue")
                    else "pending"
                ),
            )
        )

    if install_seed:
        # Names match scripts/seed_memory_discipline.py — exposed for the report
        # without importing the module (keep the installer dependency surface
        # narrow). Truth is in the seed script.
        plan.seed_rules = [
            "graph-tools-first",
            "search-before-write",
            "capability-suggestion-on-write",
            "maintain-plan-steps",
        ]

    return plan


# ============================================================
# Apply step
# ============================================================


def _backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    suffix = time.strftime(".bak-%Y%m%d-%H%M%S", time.gmtime())
    backup = path.with_suffix(path.suffix + suffix)
    shutil.copy2(path, backup)
    return backup


def apply_hooks(plan: InstallPlan, *, backup: bool) -> None:
    """Write the hook block to .claude/settings.json. Idempotent."""
    pending = [h for h in plan.hooks if h.status == "pending"]
    settings = _load_settings(plan.settings_path)
    removed_legacy = _remove_legacy_inject_hooks(settings)
    if not pending and not removed_legacy:
        return
    plan.settings_path.parent.mkdir(parents=True, exist_ok=True)
    if backup:
        _backup_file(plan.settings_path)
    for change in pending:
        if "inject_memory_brief" in change.command:
            _remove_matching_hook_entries(settings, change.event, "inject_memory_brief")
        elif "pre_tool_use_check" in change.command:
            _remove_matching_hook_entries(settings, change.event, "pre_tool_use_check")
        elif "post_edit_enqueue" in change.command:
            _remove_matching_hook_entries(settings, change.event, "post_edit_enqueue")
        _add_hook(
            settings,
            event=change.event,
            matcher=change.matcher,
            command=change.command,
        )
        change.status = "applied"
    plan.settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def apply_seed(plan: InstallPlan) -> dict[str, str]:
    """Apply root migrations + seed discipline rules. Idempotent."""
    if plan.db_path is None or not plan.workspace_id:
        return {"status": "skipped", "reason": "workspace not registered for this project_root"}
    db_path = plan.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    reflex_inserted = 0
    try:
        from scripts.seed_memory_discipline import seed_discipline  # noqa: PLC0415

        from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415
        from agent_memory_lite.bootstrap.project_memory_seed import (  # noqa: PLC0415
            seed_neutral_project_memory,
        )

        apply_migrations(conn)
        results = seed_discipline(conn, workspace_id=plan.workspace_id)
        seed_neutral_project_memory(conn, workspace_id=plan.workspace_id)
        try:
            from agent_memory_lite.bootstrap.project_memory_seed_reflexes import (  # noqa: PLC0415
                seed_reflex_rules,
            )

            reflex_inserted = seed_reflex_rules(conn, workspace_id=plan.workspace_id)
        except Exception:
            reflex_inserted = 0
    except sqlite3.Error as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        conn.close()
    inserted = sum(1 for r in results if r.status == "inserted")
    skipped = sum(1 for r in results if r.status == "skipped")
    return {
        "status": "ok",
        "inserted": str(inserted),
        "skipped": str(skipped),
        "total": str(len(results)),
        "reflex_inserted": str(reflex_inserted),
    }


# ============================================================
# CLI
# ============================================================


def render_human(
    plan: InstallPlan,
    *,
    applied: bool,
    seed_result: dict[str, str] | None,
) -> str:
    lines = [
        f"# Memory hooks installer — project: {plan.project_root}",
        f"workspace_id = {plan.workspace_id or '<not registered>'}",
        f"db_path      = {plan.db_path or '<none>'}",
        f"settings_path= {plan.settings_path}",
        "",
        "## Hooks",
    ]
    if not plan.hooks:
        lines.append("  (hook installation disabled)")
    for h in plan.hooks:
        marker = "[+]" if h.status == "applied" else "[=]" if h.status == "skipped" else "[ ]"
        matcher_part = f" matcher={h.matcher!r}" if h.matcher else ""
        lines.append(f"  {marker} {h.event}{matcher_part}")
        lines.append(f"      command: {h.command}")
    lines.append("")
    lines.append("## Seed (discipline rules)")
    if not plan.seed_rules:
        lines.append("  (seed disabled)")
    elif seed_result is None:
        lines.append("  pending: " + ", ".join(plan.seed_rules))
    else:
        lines.append(
            f"  {seed_result.get('status')}: inserted={seed_result.get('inserted')} "
            f"skipped={seed_result.get('skipped')} / "
            f"{seed_result.get('total')}"
        )
        if seed_result.get("status") == "skipped":
            lines.append(f"  reason: {seed_result.get('reason')}")
    lines.append("")
    if applied:
        lines.append("Mode: --apply (changes WRITTEN to disk)")
    else:
        lines.append("Mode: dry-run (no writes — pass --apply to commit)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install memory hooks + seed discipline rules for one project."
    )
    parser.add_argument("--project", required=True, type=Path, help="Project root path.")
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to bake into the hook commands (default: current).",
    )
    parser.add_argument("--apply", action="store_true", help="Write changes to disk.")
    parser.add_argument(
        "--backup-first",
        action="store_true",
        help="Copy .claude/settings.json next to itself before modifying.",
    )
    parser.add_argument("--no-hooks", action="store_true", help="Skip hook wiring.")
    parser.add_argument("--no-seed", action="store_true", help="Skip seed step.")
    parser.add_argument(
        "--workspaces-file",
        type=Path,
        help="Override workspace registry path (default: ~/.agent_memory/workspaces.json).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    args = parser.parse_args(argv)

    project_root = args.project.resolve()
    if not project_root.exists():
        sys.stderr.write(f"project root not found: {project_root}\n")
        return 2

    plan = build_plan(
        project_root=project_root,
        python_bin=args.python,
        install_hooks=not args.no_hooks,
        install_seed=not args.no_seed,
        workspaces_file=args.workspaces_file,
    )

    seed_result: dict[str, str] | None = None
    if args.apply:
        if plan.hooks:
            apply_hooks(plan, backup=args.backup_first)
        if plan.seed_rules:
            seed_result = apply_seed(plan)

    if args.json:
        payload = plan.to_dict()
        payload["applied"] = args.apply
        if seed_result is not None:
            payload["seed_result"] = seed_result
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(render_human(plan, applied=args.apply, seed_result=seed_result) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
