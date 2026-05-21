"""scripts/setup_agent.py --doctor tests.

Locks the drift-detection contract that caught the copyBot incident
on 2026-05-20: a deprecated ``inject_memory_context.py`` override in
.claude/settings.local.json was silently masking the global brief
hook, leaving the agent without memory for ~2.5 hours.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Import scripts/_setup_doctor.py directly (it's not on sys.path).
_SPEC = importlib.util.spec_from_file_location(
    "setup_doctor_under_test",
    Path(__file__).resolve().parents[3] / "scripts" / "_setup_doctor.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
_doctor = importlib.util.module_from_spec(_SPEC)
sys.modules["setup_doctor_under_test"] = _doctor
_SPEC.loader.exec_module(_doctor)


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> dict:
    """Build a minimal-but-valid workspace under tmp_path and return its
    registry entry. Every test starts from this clean baseline and
    introduces just the drift it cares about."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".claude").mkdir()
    settings = {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": '"py" "/path/to/scripts/inject_memory_brief.py"',
                        }
                    ]
                }
            ]
        },
        "mcpServers": {"agent-memory-lite": {"env": {"MEMORY_WORKSPACE_ID": "tw"}}},
    }
    (root / ".claude" / "settings.json").write_text(json.dumps(settings))
    db = root / ".agent_memory" / "memory.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT);
        CREATE TABLE behavior_instructions (workspace_id TEXT, name TEXT, active INT, applies_to_json TEXT);
        INSERT INTO behavior_instructions VALUES
            ('tw', 'pretooluse:read-before-edit', 1, '[]'),
            ('tw', 'pretooluse:search-before-architectural-write', 1, '[]'),
            ('tw', 'pretooluse:decision-must-have-provenance', 1, '[]'),
            ('tw', 'pretooluse:no-magic-number-in-strategy', 1, '[]');
        """
    )
    conn.commit()
    conn.close()
    vec = root / ".agent_memory" / "vectors.lance"
    vec.mkdir()
    return {
        "id": "tw",
        "project_root": str(root),
        "db_path": str(db),
        "vector_path": str(vec),
    }


def _migrations_dir(tmp_path: Path) -> Path:
    """Empty migrations dir so no migration is ever pending in tests
    (we exercise the pending path via dedicated test only)."""
    d = tmp_path / "migrations"
    d.mkdir()
    return d


def test_clean_workspace_reports_ok(tmp_workspace, tmp_path) -> None:
    """Baseline fixture is configured correctly — doctor must say ok."""
    report = _doctor.run_workspace_check(tmp_workspace, migrations_dir=_migrations_dir(tmp_path))
    assert report.status == "ok"
    assert report.findings[0].code == "clean"


def test_deprecated_hook_script_is_critical(tmp_workspace, tmp_path) -> None:
    """The exact incident we just fixed: settings.local.json points at
    inject_memory_context.py — must surface as critical."""
    local = Path(tmp_workspace["project_root"]) / ".claude" / "settings.local.json"
    local.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"command": '"py" "/old/scripts/inject_memory_context.py"'}]}
                    ]
                }
            }
        )
    )
    report = _doctor.run_workspace_check(tmp_workspace, migrations_dir=_migrations_dir(tmp_path))
    assert report.status == "critical"
    codes = [f.code for f in report.findings]
    assert "deprecated_hook_script" in codes


def test_missing_project_root_is_critical(tmp_path) -> None:
    """A registry entry pointing at a nuked project_root → critical
    so the operator notices the dangling registration."""
    report = _doctor.run_workspace_check(
        {"id": "x", "project_root": str(tmp_path / "nope"), "db_path": "", "vector_path": ""},
        migrations_dir=_migrations_dir(tmp_path),
    )
    assert report.status == "critical"
    assert any(f.code == "missing_path" for f in report.findings)


def test_mcp_workspace_id_mismatch_warns(tmp_workspace, tmp_path) -> None:
    """settings.json claims a workspace_id different from the registry
    id — agent will write to the wrong DB."""
    settings_path = Path(tmp_workspace["project_root"]) / ".claude" / "settings.json"
    s = json.loads(settings_path.read_text())
    s["mcpServers"]["agent-memory-lite"]["env"]["MEMORY_WORKSPACE_ID"] = "other_ws"
    settings_path.write_text(json.dumps(s))
    report = _doctor.run_workspace_check(tmp_workspace, migrations_dir=_migrations_dir(tmp_path))
    assert any(f.code == "mcp_workspace_mismatch" for f in report.findings)


def test_pending_migration_warns(tmp_workspace, tmp_path) -> None:
    """A migration file on disk that wasn't applied to the DB → warning."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "0099_some_new.sql").write_text("CREATE TABLE IF NOT EXISTS dummy (id TEXT);")
    report = _doctor.run_workspace_check(tmp_workspace, migrations_dir=mig)
    assert any(f.code == "pending_migrations" for f in report.findings)


def test_missing_enforcement_rule_warns(tmp_workspace, tmp_path) -> None:
    """Disable one of the 4 expected pretooluse rules → warning."""
    conn = sqlite3.connect(tmp_workspace["db_path"])
    conn.execute(
        "UPDATE behavior_instructions SET active=0 WHERE name='pretooluse:read-before-edit'"
    )
    conn.commit()
    conn.close()
    report = _doctor.run_workspace_check(tmp_workspace, migrations_dir=_migrations_dir(tmp_path))
    assert any(f.code == "missing_enforcement_rule" for f in report.findings)


def test_legacy_0001_init_does_not_false_positive(tmp_workspace, tmp_path) -> None:
    """The doctor must NOT flag canonical/0001_init as pending when the
    DB has applied root 0001_init — they create the same tables."""
    conn = sqlite3.connect(tmp_workspace["db_path"])
    conn.execute("INSERT INTO schema_migrations VALUES ('0001_init', 'now')")
    conn.commit()
    conn.close()
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "canonical").mkdir()
    (mig / "canonical" / "0001_init.sql").write_text("CREATE TABLE IF NOT EXISTS x (id TEXT);")
    report = _doctor.run_workspace_check(tmp_workspace, migrations_dir=mig)
    assert not any(
        f.code == "pending_migrations" and "0001_init" in f.detail for f in report.findings
    )


def test_unknown_hook_script_warns(tmp_workspace, tmp_path) -> None:
    """A hook pointing at a custom .py file not in the allowlist → warning
    (operator confirms it is intentional or removes the override)."""
    settings_path = Path(tmp_workspace["project_root"]) / ".claude" / "settings.json"
    s = json.loads(settings_path.read_text())
    s["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] = (
        '"py" "/custom/scripts/operator_special_hook.py"'
    )
    settings_path.write_text(json.dumps(s))
    report = _doctor.run_workspace_check(tmp_workspace, migrations_dir=_migrations_dir(tmp_path))
    assert any(f.code == "unknown_hook_script" for f in report.findings)


def test_exit_code_for_returns_critical_when_any_critical() -> None:
    """0 healthy / 1 warnings only / 2 any critical."""
    ok = _doctor.WorkspaceReport(workspace_id="a", project_root="")
    ok.findings.append(_doctor.Finding("ok", "clean", ""))
    warn = _doctor.WorkspaceReport(workspace_id="b", project_root="")
    warn.findings.append(_doctor.Finding("warning", "x", ""))
    crit = _doctor.WorkspaceReport(workspace_id="c", project_root="")
    crit.findings.append(_doctor.Finding("critical", "y", ""))
    assert _doctor.exit_code_for([ok]) == 0
    assert _doctor.exit_code_for([ok, warn]) == 1
    assert _doctor.exit_code_for([ok, warn, crit]) == 2
    assert _doctor.exit_code_for([]) == 0  # empty list = healthy


def test_render_json_round_trips(tmp_workspace, tmp_path) -> None:
    """render_json output must parse back into a dict the operator can pipe."""
    report = _doctor.run_workspace_check(tmp_workspace, migrations_dir=_migrations_dir(tmp_path))
    payload = json.loads(_doctor.render_json([report]))
    assert payload["workspaces"][0]["workspace_id"] == "tw"
    assert (
        payload["summary"]["ok"] + payload["summary"]["warning"] + payload["summary"]["critical"]
        == 1
    )


def test_render_text_emits_summary_line(tmp_workspace, tmp_path) -> None:
    """Plain-text rendering must include a Summary: line at the bottom."""
    report = _doctor.run_workspace_check(tmp_workspace, migrations_dir=_migrations_dir(tmp_path))
    text = _doctor.render_text([report])
    assert "Summary:" in text
    assert "ok=1" in text


# ============================================================
# check_env_http_workspace — .env <-> DB manifest drift (v3.7)
# ============================================================


def _make_repo_env(
    tmp_path: Path,
    *,
    env_workspace: str,
    manifest_workspace: str | None = None,
    make_db: bool = True,
) -> Path:
    """Build a fake repo dir: a .env file and (optionally) a DB whose
    workspace_manifest row carries ``manifest_workspace``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text(
        "# local-only memory service\n"
        f"MEMORY_WORKSPACE_ID={env_workspace}\n"
        "MEMORY_DB_PATH=./.agent_memory/memory.db\n",
        encoding="utf-8",
    )
    if make_db:
        db = repo / ".agent_memory" / "memory.db"
        db.parent.mkdir()
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE workspace_manifest ("
            "id INTEGER PRIMARY KEY CHECK(id = 1), workspace_id TEXT NOT NULL, "
            "db_uuid TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        if manifest_workspace is not None:
            conn.execute(
                "INSERT INTO workspace_manifest VALUES (1, ?, 'uuid', 'now', 'now')",
                (manifest_workspace,),
            )
        conn.commit()
        conn.close()
    return repo


def test_env_workspace_matches_manifest_is_ok(tmp_path) -> None:
    repo = _make_repo_env(tmp_path, env_workspace="proj", manifest_workspace="proj")
    report = _doctor.check_env_http_workspace(repo)
    assert report.status == "ok"


def test_env_workspace_mismatch_is_critical(tmp_path) -> None:
    """The exact crash this v3.7 follow-up closes: .env says 'default'
    but the DB manifest is a named workspace -> HTTP service won't start."""
    repo = _make_repo_env(tmp_path, env_workspace="default", manifest_workspace="proj")
    report = _doctor.check_env_http_workspace(repo)
    assert report.status == "critical"
    assert any(f.code == "env_workspace_manifest_mismatch" for f in report.findings)


def test_env_without_db_is_ok(tmp_path) -> None:
    """No DB yet -> nothing to mismatch; it will be created consistent."""
    repo = _make_repo_env(tmp_path, env_workspace="default", make_db=False)
    report = _doctor.check_env_http_workspace(repo)
    assert report.status == "ok"


def test_env_db_without_manifest_row_is_ok(tmp_path) -> None:
    """DB exists but no manifest stamped yet -> no drift to report."""
    repo = _make_repo_env(tmp_path, env_workspace="default", manifest_workspace=None)
    report = _doctor.check_env_http_workspace(repo)
    assert report.status == "ok"


def test_env_check_absent_env_file_is_ok(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    report = _doctor.check_env_http_workspace(repo)
    assert report.status == "ok"
