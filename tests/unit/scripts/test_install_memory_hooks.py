"""Unit tests for scripts/install_memory_hooks.py.

Covers:

* ``build_plan`` produces a complete plan with both hook entries
  and the 3 seed rule names
* Re-running the plan after apply marks hooks as ``skipped``
  (idempotency)
* ``apply_hooks`` writes the settings.json structure Claude Code
  expects (events / matcher / command)
* ``apply_seed`` runs the v3 schema + seeds 3 rules; idempotent
* Backup file is created when ``--backup-first``
* Dry-run does NOT touch disk
* main() with --apply / --no-hooks / --no-seed / --json
* Missing project root → exit 2
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from scripts import install_memory_hooks as installer

# ============================================================
# Helpers
# ============================================================


def _write_registry(path: Path, entries: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "workspaces": [
            {
                "id": e["id"],
                "db_path": e["db_path"],
                "vector_path": e.get("vector_path", ""),
                "label": e.get("label", ""),
                "project_root": e["project_root"],
                "registered_at": "",
                "last_seen_at": "",
                "extra": {},
            }
            for e in entries
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


@pytest.fixture
def registry_file(tmp_path: Path, project: Path) -> Path:
    """Registry with one entry pointing at our temp project + a v3-schema DB path."""
    db_path = tmp_path / "ws.db"
    reg = tmp_path / "workspaces.json"
    _write_registry(
        reg,
        [
            {
                "id": "test-ws",
                "db_path": str(db_path),
                "vector_path": str(tmp_path / "ws.lance"),
                "project_root": str(project),
            }
        ],
    )
    return reg


# ============================================================
# build_plan
# ============================================================


def test_build_plan_includes_both_hooks(project: Path, registry_file: Path) -> None:
    plan = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=True,
        install_seed=True,
        workspaces_file=registry_file,
    )
    events = {h.event for h in plan.hooks}
    assert events == {"UserPromptSubmit", "PostToolUse"}
    assert all(h.status == "pending" for h in plan.hooks)
    assert plan.workspace_id == "test-ws"
    assert plan.seed_rules == [
        "graph-tools-first",
        "search-before-write",
        "capability-link-on-write",
    ]


def test_build_plan_marks_existing_hooks_skipped(project: Path, registry_file: Path) -> None:
    """If settings.json already has the v3 hook entries, plan reports skipped."""
    existing = {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": '"python" "...inject_memory_brief.py"',
                        }
                    ]
                }
            ]
        }
    }
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps(existing), encoding="utf-8")
    plan = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=True,
        install_seed=False,
        workspaces_file=registry_file,
    )
    user_prompt = next(h for h in plan.hooks if h.event == "UserPromptSubmit")
    post_tool = next(h for h in plan.hooks if h.event == "PostToolUse")
    assert user_prompt.status == "skipped"
    assert post_tool.status == "pending"  # not yet installed


def test_build_plan_no_workspace_when_not_registered(project: Path, tmp_path: Path) -> None:
    empty_reg = tmp_path / "empty.json"
    _write_registry(empty_reg, [])
    plan = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=True,
        install_seed=True,
        workspaces_file=empty_reg,
    )
    assert plan.workspace_id == ""
    assert plan.db_path is None


def test_build_plan_no_hooks_when_disabled(project: Path, registry_file: Path) -> None:
    plan = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=False,
        install_seed=True,
        workspaces_file=registry_file,
    )
    assert plan.hooks == []
    assert len(plan.seed_rules) == 3


# ============================================================
# apply_hooks
# ============================================================


def test_apply_hooks_writes_expected_structure(project: Path, registry_file: Path) -> None:
    plan = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=True,
        install_seed=False,
        workspaces_file=registry_file,
    )
    installer.apply_hooks(plan, backup=False)
    settings = json.loads(plan.settings_path.read_text(encoding="utf-8"))
    user_prompt_entries = settings["hooks"]["UserPromptSubmit"]
    post_tool_entries = settings["hooks"]["PostToolUse"]
    assert len(user_prompt_entries) == 1
    assert len(post_tool_entries) == 1
    assert post_tool_entries[0]["matcher"].startswith("Edit|Write|NotebookEdit")
    assert "inject_memory_brief" in user_prompt_entries[0]["hooks"][0]["command"]
    assert "post_edit_enqueue" in post_tool_entries[0]["hooks"][0]["command"]
    # All hooks now marked applied.
    assert all(h.status == "applied" for h in plan.hooks)


def test_apply_hooks_is_idempotent(project: Path, registry_file: Path) -> None:
    """Two applies produce the same settings (no duplicate entries)."""
    plan1 = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=True,
        install_seed=False,
        workspaces_file=registry_file,
    )
    installer.apply_hooks(plan1, backup=False)
    plan2 = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=True,
        install_seed=False,
        workspaces_file=registry_file,
    )
    installer.apply_hooks(plan2, backup=False)
    settings = json.loads(plan2.settings_path.read_text(encoding="utf-8"))
    assert len(settings["hooks"]["UserPromptSubmit"]) == 1
    assert len(settings["hooks"]["PostToolUse"]) == 1


def test_apply_hooks_backup_creates_backup(project: Path, registry_file: Path) -> None:
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text('{"existing": true}', encoding="utf-8")
    plan = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=True,
        install_seed=False,
        workspaces_file=registry_file,
    )
    installer.apply_hooks(plan, backup=True)
    backups = list(settings_path.parent.glob("settings.json.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"existing": true}'


def test_apply_hooks_preserves_unrelated_settings(project: Path, registry_file: Path) -> None:
    """Operator's existing settings.json keys must survive the install."""
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"theme": "dark", "permissions": {"allow": ["Bash"]}}),
        encoding="utf-8",
    )
    plan = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=True,
        install_seed=False,
        workspaces_file=registry_file,
    )
    installer.apply_hooks(plan, backup=False)
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["theme"] == "dark"
    assert settings["permissions"]["allow"] == ["Bash"]
    assert "hooks" in settings


# ============================================================
# apply_seed
# ============================================================


def test_apply_seed_creates_schema_and_seeds_rules(
    project: Path, registry_file: Path, tmp_path: Path
) -> None:
    plan = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=False,
        install_seed=True,
        workspaces_file=registry_file,
    )
    result = installer.apply_seed(plan)
    assert result["status"] == "ok"
    assert int(result["inserted"]) == 3
    # Verify rules landed in the DB.
    conn = sqlite3.connect(plan.db_path)
    try:
        rows = conn.execute(
            "SELECT name, pinned FROM behaviors WHERE workspace_id = ?",
            (plan.workspace_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    assert all(pinned == 1 for _, pinned in rows)


def test_apply_seed_idempotent_on_second_pass(project: Path, registry_file: Path) -> None:
    plan = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=False,
        install_seed=True,
        workspaces_file=registry_file,
    )
    installer.apply_seed(plan)
    result = installer.apply_seed(plan)
    assert result["status"] == "ok"
    assert int(result["inserted"]) == 0
    assert int(result["skipped"]) == 3


def test_apply_seed_skipped_when_workspace_unregistered(project: Path, tmp_path: Path) -> None:
    empty_reg = tmp_path / "empty.json"
    _write_registry(empty_reg, [])
    plan = installer.build_plan(
        project_root=project,
        python_bin="python",
        install_hooks=False,
        install_seed=True,
        workspaces_file=empty_reg,
    )
    result = installer.apply_seed(plan)
    assert result["status"] == "skipped"
    assert "not registered" in result["reason"]


# ============================================================
# main() / dry-run / json
# ============================================================


def test_main_dry_run_does_not_touch_disk(
    project: Path,
    registry_file: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    settings_path = project / ".claude" / "settings.json"
    rc = installer.main(
        [
            "--project",
            str(project),
            "--workspaces-file",
            str(registry_file),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert not settings_path.exists()  # no write happened
    # DB should be untouched too — file may or may not exist
    plan_db = json.loads(registry_file.read_text(encoding="utf-8"))["workspaces"][0]["db_path"]
    assert not Path(plan_db).exists()


def test_main_apply_writes_settings_and_seeds(
    project: Path,
    registry_file: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    rc = installer.main(
        [
            "--project",
            str(project),
            "--workspaces-file",
            str(registry_file),
            "--apply",
        ]
    )
    assert rc == 0
    settings = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "UserPromptSubmit" in settings["hooks"]
    assert "PostToolUse" in settings["hooks"]
    db_path = json.loads(registry_file.read_text(encoding="utf-8"))["workspaces"][0]["db_path"]
    assert Path(db_path).exists()


def test_main_no_hooks_flag(
    project: Path,
    registry_file: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    rc = installer.main(
        [
            "--project",
            str(project),
            "--workspaces-file",
            str(registry_file),
            "--no-hooks",
            "--apply",
        ]
    )
    assert rc == 0
    # No settings.json should be created.
    assert not (project / ".claude" / "settings.json").exists()
    # But seed should run.
    db_path = json.loads(registry_file.read_text(encoding="utf-8"))["workspaces"][0]["db_path"]
    assert Path(db_path).exists()


def test_main_no_seed_flag(
    project: Path,
    registry_file: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    rc = installer.main(
        [
            "--project",
            str(project),
            "--workspaces-file",
            str(registry_file),
            "--no-seed",
            "--apply",
        ]
    )
    assert rc == 0
    # Hooks installed.
    assert (project / ".claude" / "settings.json").exists()
    # DB should NOT have been created — seed disabled.
    db_path = json.loads(registry_file.read_text(encoding="utf-8"))["workspaces"][0]["db_path"]
    assert not Path(db_path).exists()


def test_main_json_output(
    project: Path,
    registry_file: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    rc = installer.main(
        [
            "--project",
            str(project),
            "--workspaces-file",
            str(registry_file),
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "hooks" in payload
    assert "seed_rules" in payload
    assert payload["applied"] is False


def test_main_missing_project_returns_two(tmp_path: Path) -> None:
    rc = installer.main(["--project", str(tmp_path / "nope")])
    assert rc == 2
