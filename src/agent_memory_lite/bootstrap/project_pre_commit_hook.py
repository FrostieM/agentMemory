"""Install the pre-commit auto-ingest hook into a target git project.

Called by ``scripts/setup_agent.py --project`` so the operator never has
to copy the hook manually or remember to set ``MEMORY_WORKSPACE_ID``.

The hook source is copied from the agent-memory-lite repo's
``scripts/git_hooks/pre-commit`` (the single template) with one line
prepended: ``MEMORY_WORKSPACE_ID="${MEMORY_WORKSPACE_ID:-<workspace>}"``.
The env variable still wins if set explicitly, but the project's
workspace_id is the default — so a fresh shell with no env config still
indexes the right workspace.

Idempotent: rewriting the hook with the same workspace_id is a no-op
write; rewriting with a different workspace_id refreshes the default.
Graceful: if the target is not a git working tree (no ``.git/``), the
function logs and returns without raising — installing memory in a
non-git directory is fine, just no hook to install.
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

HOOK_TEMPLATE_RELATIVE = "scripts/git_hooks/pre-commit"
TARGET_HOOK_RELATIVE = ".git/hooks/pre-commit"
WORKSPACE_LINE_MARKER = "# >>> agent-memory-lite project workspace_id (auto)"
WORKSPACE_LINE_END = "# <<< agent-memory-lite project workspace_id (auto)"


def _build_workspace_block(workspace_id: str) -> str:
    """Return the prepended block that hard-defaults MEMORY_WORKSPACE_ID."""
    safe = workspace_id.replace('"', '\\"')
    return (
        f"{WORKSPACE_LINE_MARKER}\n"
        f'export MEMORY_WORKSPACE_ID="${{MEMORY_WORKSPACE_ID:-{safe}}}"\n'
        f"{WORKSPACE_LINE_END}\n"
    )


def _insert_workspace_block(template_text: str, workspace_id: str) -> str:
    """Insert the workspace-id default block right after the shebang.

    If a previous block is present (idempotent re-install), it is
    replaced in place — never duplicated.
    """
    block = _build_workspace_block(workspace_id)
    if WORKSPACE_LINE_MARKER in template_text and WORKSPACE_LINE_END in template_text:
        before, _, rest = template_text.partition(WORKSPACE_LINE_MARKER)
        _, _, after = rest.partition(WORKSPACE_LINE_END)
        return before + block + after.lstrip("\n")
    lines = template_text.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        return lines[0] + block + "".join(lines[1:])
    return block + template_text


def install_project_pre_commit_hook(
    *, repo_root: Path, project_root: Path, workspace_id: str
) -> dict[str, object]:
    """Copy the pre-commit auto-ingest hook into ``project_root/.git/hooks/``.

    Returns a dict with ``status`` (installed / refreshed / skipped_no_git /
    skipped_no_template), ``hook_path``, and ``workspace_id`` for the
    caller to log.
    """
    template_path = repo_root / HOOK_TEMPLATE_RELATIVE
    if not template_path.is_file():
        return {
            "status": "skipped_no_template",
            "hook_path": None,
            "workspace_id": workspace_id,
            "reason": f"template missing at {template_path}",
        }

    git_dir = project_root / ".git"
    if not git_dir.exists():
        return {
            "status": "skipped_no_git",
            "hook_path": None,
            "workspace_id": workspace_id,
            "reason": f"{project_root} is not a git working tree",
        }

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    target = project_root / TARGET_HOOK_RELATIVE

    template_text = template_path.read_text(encoding="utf-8")
    new_text = _insert_workspace_block(template_text, workspace_id)

    existed = target.is_file()
    if existed and target.read_text(encoding="utf-8") == new_text:
        return {
            "status": "unchanged",
            "hook_path": str(target),
            "workspace_id": workspace_id,
        }

    target.write_text(new_text, encoding="utf-8")
    current_mode = target.stat().st_mode
    target.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return {
        "status": "refreshed" if existed else "installed",
        "hook_path": str(target),
        "workspace_id": workspace_id,
    }


def fallback_copy_pre_push_if_present(*, repo_root: Path, project_root: Path) -> None:
    """Best-effort install of the project's pre-push crash-test hook too.

    Only runs when ``project_root == repo_root`` (we are installing inside
    the agent-memory-lite repo itself); for arbitrary downstream projects
    the crash-test hook is not relevant, so we skip silently.
    """
    if project_root.resolve() != repo_root.resolve():
        return
    src = repo_root / "scripts" / "git_hooks" / "pre-push"
    if not src.is_file():
        return
    dst = project_root / ".git" / "hooks" / "pre-push"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file() and dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8"):
        return
    shutil.copy2(src, dst)
    current_mode = dst.stat().st_mode
    dst.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
