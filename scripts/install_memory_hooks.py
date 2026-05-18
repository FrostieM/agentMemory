"""Install the v3 hook + seed stack for one project. Operator-facing.

This is the deployment surface for Phase 5 of the v3 plan.  It wires
the four discipline layers (impact_check primitive, lint advisory,
brief identity line, pinned seed rules) into a project's actual
Claude Code installation:

  1. Project-scoped ``.claude/settings.json`` gains a
     ``UserPromptSubmit`` hook → ``inject_memory_brief.py`` and a
     ``PostToolUse`` hook → ``post_edit_enqueue.py``.
  2. The workspace's SQLite DB has the v3 schema applied (if not
     already) and the 3 pinned discipline behaviors seeded.

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

import argparse
import contextlib
import json
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Make sibling scripts importable when this file is run directly. Tests use
# pytest's auto-rootdir, but `python scripts/install_memory_hooks.py` runs with
# only ``scripts/`` on sys.path, which means ``from scripts.seed_memory_discipline``
# fails. Prepend the repo root so both run styles work.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent_memory_lite.config.workspace_registry import WorkspaceRegistry  # noqa: E402

# Force UTF-8 stdout — the human render has → / em-dash / non-ASCII
# glyphs that crash a default Windows cp1251 console.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
with contextlib.suppress(AttributeError, ValueError):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "migrations" / "canonical" / "0001_init.sql"
BRIEF_HOOK_SCRIPT = REPO_ROOT / "scripts" / "inject_memory_brief.py"
POSTEDIT_HOOK_SCRIPT = REPO_ROOT / "scripts" / "post_edit_enqueue.py"
SEED_SCRIPT = REPO_ROOT / "scripts" / "seed_memory_discipline.py"
POSTTOOLUSE_MATCHER = "Edit|Write|NotebookEdit|MultiEdit"


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


def _hook_already_installed(settings: dict[str, Any], event: str, command_substr: str) -> bool:
    """True if any hook under ``event`` already names ``command_substr``."""
    hooks_root = settings.get("hooks")
    if not isinstance(hooks_root, dict):
        return False
    entries = hooks_root.get(event)
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for inner in entry.get("hooks", []) or []:
            if not isinstance(inner, dict):
                continue
            cmd = str(inner.get("command", ""))
            if command_substr in cmd:
                return True
    return False


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
            "capability-link-on-write",
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
    if not pending:
        return
    plan.settings_path.parent.mkdir(parents=True, exist_ok=True)
    if backup:
        _backup_file(plan.settings_path)
    settings = _load_settings(plan.settings_path)
    for change in pending:
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
    """Apply v3 schema (if missing) + seed discipline rules. Idempotent."""
    if plan.db_path is None or not plan.workspace_id:
        return {"status": "skipped", "reason": "workspace not registered for this project_root"}
    db_path = plan.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        # Ensure v3 schema is applied. The v3 schema script uses
        # CREATE TABLE IF NOT EXISTS, so it's idempotent on an existing DB.
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        from scripts.seed_memory_discipline import seed_discipline  # noqa: PLC0415

        results = seed_discipline(conn, workspace_id=plan.workspace_id)
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
        f"# v3 hooks installer — project: {plan.project_root}",
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
    lines.append("## Seed (v3 discipline rules)")
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
        description="Install v3 hooks + seed discipline rules for one project."
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
