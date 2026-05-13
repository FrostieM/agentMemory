"""Unit tests for per-project pre-commit hook installer."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agent_memory_lite.bootstrap.project_pre_commit_hook import (
    WORKSPACE_LINE_END,
    WORKSPACE_LINE_MARKER,
    _insert_workspace_block,
    install_project_pre_commit_hook,
)

TEMPLATE_TEXT = """#!/usr/bin/env bash
# Pre-commit hook: auto-ingest staged source files.

set -u

if [[ "${MEMORY_SKIP_PRECOMMIT_INGEST:-0}" == "1" ]]; then
    exit 0
fi

WORKSPACE_ID="${MEMORY_WORKSPACE_ID:-}"
"""


def _make_template(repo: Path, text: str = TEMPLATE_TEXT) -> Path:
    hooks_src = repo / "scripts" / "git_hooks"
    hooks_src.mkdir(parents=True, exist_ok=True)
    template = hooks_src / "pre-commit"
    template.write_text(text, encoding="utf-8")
    return template


def _make_git_project(root: Path) -> None:
    (root / ".git" / "hooks").mkdir(parents=True, exist_ok=True)


def test_insert_workspace_block_after_shebang() -> None:
    out = _insert_workspace_block(TEMPLATE_TEXT, "myproj")
    lines = out.splitlines()
    assert lines[0].startswith("#!"), "shebang must remain first"
    assert lines[1] == WORKSPACE_LINE_MARKER
    assert "MEMORY_WORKSPACE_ID:-myproj" in lines[2]
    assert lines[3] == WORKSPACE_LINE_END


def test_insert_workspace_block_is_idempotent() -> None:
    once = _insert_workspace_block(TEMPLATE_TEXT, "myproj")
    twice = _insert_workspace_block(once, "myproj")
    assert once == twice, "second insert must be a no-op (block replaced in place)"


def test_insert_workspace_block_refreshes_workspace_id_on_change() -> None:
    first = _insert_workspace_block(TEMPLATE_TEXT, "old")
    refreshed = _insert_workspace_block(first, "new")
    assert "MEMORY_WORKSPACE_ID:-old" not in refreshed
    assert "MEMORY_WORKSPACE_ID:-new" in refreshed
    assert refreshed.count(WORKSPACE_LINE_MARKER) == 1


def test_insert_workspace_block_escapes_quotes_in_id() -> None:
    out = _insert_workspace_block(TEMPLATE_TEXT, 'a"b')
    assert 'MEMORY_WORKSPACE_ID:-a\\"b' in out


def test_install_into_real_git_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    project = tmp_path / "proj"
    _make_template(repo)
    _make_git_project(project)

    result = install_project_pre_commit_hook(
        repo_root=repo, project_root=project, workspace_id="myproj"
    )
    assert result["status"] == "installed"
    hook = Path(str(result["hook_path"]))
    assert hook.is_file()
    body = hook.read_text(encoding="utf-8")
    assert "MEMORY_WORKSPACE_ID:-myproj" in body
    # executable bit set
    if os.name != "nt":
        assert hook.stat().st_mode & stat.S_IXUSR


def test_install_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    project = tmp_path / "proj"
    _make_template(repo)
    _make_git_project(project)

    install_project_pre_commit_hook(repo_root=repo, project_root=project, workspace_id="myproj")
    second = install_project_pre_commit_hook(
        repo_root=repo, project_root=project, workspace_id="myproj"
    )
    assert second["status"] == "unchanged"


def test_install_refresh_on_workspace_id_change(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    project = tmp_path / "proj"
    _make_template(repo)
    _make_git_project(project)

    install_project_pre_commit_hook(repo_root=repo, project_root=project, workspace_id="old")
    refreshed = install_project_pre_commit_hook(
        repo_root=repo, project_root=project, workspace_id="new"
    )
    assert refreshed["status"] == "refreshed"
    body = Path(str(refreshed["hook_path"])).read_text(encoding="utf-8")
    assert "MEMORY_WORKSPACE_ID:-new" in body
    assert "MEMORY_WORKSPACE_ID:-old" not in body


def test_install_skips_when_not_a_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    project = tmp_path / "proj"
    _make_template(repo)
    project.mkdir()  # NO .git/

    result = install_project_pre_commit_hook(repo_root=repo, project_root=project, workspace_id="x")
    assert result["status"] == "skipped_no_git"
    assert result["hook_path"] is None


def test_install_skips_when_template_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    project = tmp_path / "proj"
    repo.mkdir()  # NO scripts/git_hooks/pre-commit
    _make_git_project(project)

    result = install_project_pre_commit_hook(repo_root=repo, project_root=project, workspace_id="x")
    assert result["status"] == "skipped_no_template"


@pytest.mark.parametrize("workspace_id", ["simple", "with-dash", "with_under"])
def test_workspace_ids_round_trip(tmp_path: Path, workspace_id: str) -> None:
    repo = tmp_path / "repo"
    project = tmp_path / "proj"
    _make_template(repo)
    _make_git_project(project)

    result = install_project_pre_commit_hook(
        repo_root=repo, project_root=project, workspace_id=workspace_id
    )
    body = Path(str(result["hook_path"])).read_text(encoding="utf-8")
    assert f"MEMORY_WORKSPACE_ID:-{workspace_id}" in body
