"""scripts/setup_agent.py — align_env_workspace tests.

v3.7 follow-up: the HTTP service (``python -m agent_memory_lite``) reads
its bootstrap workspace from ``.env``, but setup only wrote the workspace
into ``.claude/settings.json`` (the MCP path). ``align_env_workspace``
closes that gap by rewriting ``.env``'s ``MEMORY_WORKSPACE_ID`` to match
the repo DB manifest — otherwise a dogfooded repo crashes the service
with ``WorkspaceManifestError``.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

# Import scripts/setup_agent.py directly (it is not on sys.path).
_SPEC = importlib.util.spec_from_file_location(
    "setup_agent_under_test",
    Path(__file__).resolve().parents[3] / "scripts" / "setup_agent.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
_setup = importlib.util.module_from_spec(_SPEC)
sys.modules["setup_agent_under_test"] = _setup
_SPEC.loader.exec_module(_setup)


def _make_repo(
    tmp_path: Path, *, env_workspace: str, manifest_workspace: str | None = None
) -> Path:
    """Build a fake repo: a .env file and (optionally) a DB whose
    workspace_manifest row carries ``manifest_workspace``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text(
        "# local-only memory service\n"
        f"MEMORY_WORKSPACE_ID={env_workspace}\n"
        "MEMORY_DB_PATH=./.agent_memory/memory.db\n",
        encoding="utf-8",
    )
    if manifest_workspace is not None:
        db = repo / ".agent_memory" / "memory.db"
        db.parent.mkdir()
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE workspace_manifest ("
            "id INTEGER PRIMARY KEY CHECK(id = 1), workspace_id TEXT NOT NULL, "
            "db_uuid TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO workspace_manifest VALUES (1, ?, 'uuid', 'now', 'now')",
            (manifest_workspace,),
        )
        conn.commit()
        conn.close()
    return repo


def _env_workspace(repo: Path) -> str:
    for line in (repo / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("MEMORY_WORKSPACE_ID="):
            return line.split("=", 1)[1]
    return "<absent>"


def test_align_rewrites_env_to_match_manifest(tmp_path: Path) -> None:
    """Repair case: .env=default but the repo DB manifest=proj — the
    HTTP service would crash, so .env is rewritten to proj."""
    repo = _make_repo(tmp_path, env_workspace="default", manifest_workspace="proj")
    _setup.align_env_workspace(repo, fallback="default")
    assert _env_workspace(repo) == "proj"


def test_align_is_noop_when_already_consistent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, env_workspace="proj", manifest_workspace="proj")
    before = (repo / ".env").read_text(encoding="utf-8")
    _setup.align_env_workspace(repo, fallback="proj")
    assert (repo / ".env").read_text(encoding="utf-8") == before


def test_align_uses_fallback_when_no_db(tmp_path: Path) -> None:
    """Fresh ``--workspace proj`` install, DB not created yet — the
    fallback workspace lands in .env so the DB is created consistent."""
    repo = _make_repo(tmp_path, env_workspace="default")
    _setup.align_env_workspace(repo, fallback="proj")
    assert _env_workspace(repo) == "proj"


def test_align_manifest_wins_over_fallback(tmp_path: Path) -> None:
    """When both exist, the DB manifest is ground truth, not the fallback."""
    repo = _make_repo(tmp_path, env_workspace="default", manifest_workspace="proj")
    _setup.align_env_workspace(repo, fallback="something_else")
    assert _env_workspace(repo) == "proj"


def test_align_noop_when_no_env_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _setup.align_env_workspace(repo, fallback="proj")  # must not raise
    assert not (repo / ".env").exists()


def test_remove_v2_inject_hook_removes_unmarked_script_path() -> None:
    settings: dict[str, object] = {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": '"python" "scripts/inject_memory_context.py"',
                        }
                    ]
                },
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": '"python" "scripts/inject_memory_brief.py"',
                        }
                    ]
                },
            ]
        }
    }

    assert _setup._remove_v2_inject_hook(settings) == 1
    remaining = settings["hooks"]["UserPromptSubmit"]
    assert len(remaining) == 1
    assert "inject_memory_brief.py" in remaining[0]["hooks"][0]["command"]


def test_install_codex_project_hooks_writes_v3_stack(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    status = _setup.install_codex_project_hooks(project, venv_python=Path("python"))

    assert status == "created"
    hooks = json.loads((project / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert "inject_memory_brief.py" in hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "pre_tool_use_check.py" in hooks["PreToolUse"][0]["hooks"][0]["command"]
    assert "post_edit_enqueue.py" in hooks["PostToolUse"][0]["hooks"][0]["command"]


def test_install_codex_project_hooks_removes_legacy_context_hook(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    hooks_path = project / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"command": '"python" "scripts/inject_memory_context.py"'}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    status = _setup.install_codex_project_hooks(project, venv_python=Path("python"))

    assert status == "updated"
    text = hooks_path.read_text(encoding="utf-8")
    assert "inject_memory_context.py" not in text
    assert "inject_memory_brief.py" in text
