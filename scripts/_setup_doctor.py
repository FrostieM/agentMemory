"""scripts/setup_agent.py --doctor — drift detection across registered workspaces.

The story behind this module: on 2026-05-20 we found that copyBot's
``.claude/settings.local.json`` still overrode ``UserPromptSubmit`` to call
``inject_memory_context.py`` — a v1.x script that returned HTTP 500 against
the current v3.4 service. The copyBot agent silently lost its memory brief
on every turn for ~2.5 hours before the operator noticed.

``--doctor`` scans every workspace in ``~/.agent_memory/workspaces.json``
and reports drift between what setup_agent.py installs today and what
each project's actual config + DB look like. It does NOT write anything;
the operator decides which findings to act on.

Exit codes:
* 0 — every workspace clean
* 1 — warnings only (deprecated config, missing optional rule)
* 2 — critical (missing file, schema drift, broken hook target)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Scripts the operator's hooks may legitimately reference. Anything outside
# this allowlist is reported as deprecated/unknown — the operator should
# either update the override or remove it so the global hook config wins.
_CURRENT_HOOK_SCRIPTS = frozenset(
    {
        "inject_memory_brief.py",
        "pre_tool_use_check.py",
        "post_edit_enqueue.py",
    }
)
_REQUIRED_PROJECT_HOOKS = {
    "UserPromptSubmit": "inject_memory_brief.py",
    "PreToolUse": "pre_tool_use_check.py",
    "PostToolUse": "post_edit_enqueue.py",
}
_DEPRECATED_HOOK_SCRIPTS = frozenset(
    {
        # Replaced by inject_memory_brief.py during the v3.0.0 brain rewrite.
        # Still on disk for archeology but returns HTTP 500 against v3.x.
        "inject_memory_context.py",
    }
)
_STALE_CONTRACT_TOKENS = (
    "memory_get_context",
    "memory_list_",
    "memory_write_",
    "memory_ingest_episode",
    "memory_update_task_state",
    "inject_memory_context.py",
)
_EXPECTED_PRETOOLUSE_RULES = frozenset(
    {
        "pretooluse:impact-check-before-read",
        "pretooluse:read-before-edit",
        "pretooluse:search-before-architectural-write",
        "pretooluse:decision-must-have-provenance",
        "pretooluse:no-magic-number-in-strategy",
    }
)
_REQUIRED_PRETOOLUSE_MATCHERS = frozenset(
    {
        "Read",
        "Edit",
        "Write",
        "MultiEdit",
        "NotebookEdit",
        "Grep",
        "Bash",
        "mcp__agent-memory-lite__memory_.*",
    }
)


@dataclass
class Finding:
    severity: str  # "ok" / "warning" / "critical"
    code: str  # short stable identifier, e.g. "stale_hook_script"
    detail: str


@dataclass
class WorkspaceReport:
    workspace_id: str
    project_root: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(f.severity == "critical" for f in self.findings):
            return "critical"
        if any(f.severity == "warning" for f in self.findings):
            return "warning"
        return "ok"


def _script_basename(command: str) -> str | None:
    """Extract the .py filename out of a hook command line."""
    for raw in command.split('"'):
        token = raw.strip()
        if token.endswith(".py"):
            return os.path.basename(token)
    return None


def _check_hook_block(
    report: WorkspaceReport,
    *,
    source: str,
    hook_type: str,
    defs: list[dict[str, object]],
) -> None:
    """Validate every hook entry under ``hooks.<type>`` of a settings file."""
    for d in defs:
        hook_list = d.get("hooks", [])
        if not isinstance(hook_list, list):
            continue
        for h in hook_list:
            if not isinstance(h, dict):
                continue
            cmd = str(h.get("command", ""))
            script = _script_basename(cmd)
            if script is None:
                report.findings.append(
                    Finding(
                        "warning",
                        "hook_no_script",
                        f"{source}: {hook_type} command does not point at a .py file",
                    )
                )
                continue
            if script in _DEPRECATED_HOOK_SCRIPTS:
                report.findings.append(
                    Finding(
                        "critical",
                        "deprecated_hook_script",
                        f"{source}: {hook_type} uses {script!r} (deprecated v1.x — replace with the current global hook)",
                    )
                )
            elif script not in _CURRENT_HOOK_SCRIPTS:
                report.findings.append(
                    Finding(
                        "warning",
                        "unknown_hook_script",
                        f"{source}: {hook_type} uses {script!r} (not in allowlist; verify it is intentional)",
                    )
                )


def _hook_defs_include_script(defs: object, expected_script: str) -> bool:
    if not isinstance(defs, list):
        return False
    for d in defs:
        if not isinstance(d, dict):
            continue
        hook_list = d.get("hooks", [])
        if not isinstance(hook_list, list):
            continue
        for h in hook_list:
            if (
                isinstance(h, dict)
                and _script_basename(str(h.get("command", ""))) == expected_script
            ):
                return True
    return False


def _pretooluse_matcher_tokens(defs: object) -> set[str]:
    tokens: set[str] = set()
    if not isinstance(defs, list):
        return tokens
    for d in defs:
        if not isinstance(d, dict):
            continue
        hook_list = d.get("hooks", [])
        if not isinstance(hook_list, list):
            continue
        if not any(
            isinstance(h, dict)
            and _script_basename(str(h.get("command", ""))) == "pre_tool_use_check.py"
            for h in hook_list
        ):
            continue
        matcher = d.get("matcher")
        if isinstance(matcher, str) and matcher:
            tokens.update(part for part in matcher.split("|") if part)
    return tokens


def _check_required_project_hooks(
    report: WorkspaceReport,
    *,
    source: str,
    hooks: dict[str, object],
) -> None:
    for hook_type, expected_script in _REQUIRED_PROJECT_HOOKS.items():
        if _hook_defs_include_script(hooks.get(hook_type), expected_script):
            continue
        report.findings.append(
            Finding(
                "warning",
                "missing_required_hook",
                f"{source}: {hook_type} missing {expected_script!r}",
            )
        )
    missing_matchers = _REQUIRED_PRETOOLUSE_MATCHERS - _pretooluse_matcher_tokens(
        hooks.get("PreToolUse")
    )
    if missing_matchers:
        report.findings.append(
            Finding(
                "warning",
                "stale_pretooluse_matcher",
                f"{source}: PreToolUse matcher missing {', '.join(sorted(missing_matchers))}",
            )
        )


def _check_paths(report: WorkspaceReport, entry: dict[str, object]) -> None:
    for key in ("project_root", "db_path", "vector_path"):
        value = entry.get(key)
        if not isinstance(value, str) or not value:
            report.findings.append(
                Finding("critical", "missing_registry_field", f"registry entry has no {key}")
            )
            continue
        if not Path(value).exists():
            report.findings.append(
                Finding(
                    "critical",
                    "missing_path",
                    f"{key} does not exist on disk: {value}",
                )
            )


def _check_settings_files(report: WorkspaceReport, project_root: Path) -> None:
    settings = project_root / ".claude" / "settings.json"
    settings_local = project_root / ".claude" / "settings.local.json"
    codex_dir = project_root / ".codex"
    codex_hooks = codex_dir / "hooks.json"
    if not settings.exists():
        report.findings.append(
            Finding(
                "warning",
                "no_project_settings",
                "<project>/.claude/settings.json missing (project will fall back to global)",
            )
        )
    for path, label in ((settings, "project"), (settings_local, "local")):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.findings.append(Finding("critical", "settings_parse_error", f"{label}: {exc}"))
            continue
        hooks_root = data.get("hooks") or {}
        if not isinstance(hooks_root, dict):
            hooks_root = {}
        for hook_type, defs in hooks_root.items():
            if not isinstance(defs, list):
                continue
            _check_hook_block(report, source=label, hook_type=hook_type, defs=defs)
        if label == "project":
            _check_required_project_hooks(report, source=label, hooks=hooks_root)
        # MCP config: workspace_id must match the registry id.
        for name, cfg in (data.get("mcpServers") or {}).items():
            env = cfg.get("env") or {}
            ws = env.get("MEMORY_WORKSPACE_ID")
            if ws and ws != report.workspace_id:
                report.findings.append(
                    Finding(
                        "warning",
                        "mcp_workspace_mismatch",
                        f"{label}.mcpServers[{name}].env.MEMORY_WORKSPACE_ID={ws!r} but registry id={report.workspace_id!r}",
                    )
                )
    _check_codex_hooks_file(report, codex_dir=codex_dir, codex_hooks=codex_hooks)


def _check_codex_hooks_file(
    report: WorkspaceReport,
    *,
    codex_dir: Path,
    codex_hooks: Path,
) -> None:
    if codex_dir.exists() and not codex_hooks.exists():
        report.findings.append(
            Finding(
                "warning",
                "missing_codex_hooks",
                "<project>/.codex/hooks.json missing (Codex Desktop will not get project hooks)",
            )
        )
    if not codex_hooks.exists():
        return
    try:
        data = json.loads(codex_hooks.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.findings.append(Finding("critical", "codex_hooks_parse_error", f"codex: {exc}"))
        return
    hooks_root = data.get("hooks") if isinstance(data, dict) else {}
    if not isinstance(hooks_root, dict):
        hooks_root = {}
    for hook_type, defs in hooks_root.items():
        if not isinstance(defs, list):
            continue
        _check_hook_block(report, source="codex", hook_type=hook_type, defs=defs)
    _check_required_project_hooks(report, source="codex", hooks=hooks_root)


def _check_agent_contract_files(report: WorkspaceReport, project_root: Path) -> None:
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = project_root / name
        if not path.exists():
            report.findings.append(Finding("warning", "missing_agent_contract", f"{name} missing"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            report.findings.append(
                Finding("critical", "agent_contract_unreadable", f"{name}: {exc}")
            )
            continue
        if "## V3-Only Active Surface" not in text:
            report.findings.append(
                Finding(
                    "warning",
                    "stale_agent_contract",
                    f"{name} missing V3-Only Active Surface section",
                )
            )
        stale = [token for token in _STALE_CONTRACT_TOKENS if token in text]
        if stale:
            report.findings.append(
                Finding(
                    "critical",
                    "stale_agent_contract",
                    f"{name} still mentions removed legacy surface: {', '.join(stale[:3])}",
                )
            )


def _check_migrations(report: WorkspaceReport, db_path: Path, migrations_dir: Path) -> None:
    if not db_path.exists() or not migrations_dir.exists():
        return  # path-check already reported the gap
    try:
        conn = sqlite3.connect(db_path)
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        conn.close()
    except sqlite3.Error as exc:
        report.findings.append(
            Finding("critical", "migrations_table_unreadable", f"{db_path.name}: {exc}")
        )
        return
    # Mirror the runtime migration runner exactly: db.migrations.apply_migrations
    # globs only the top level of migrations/ and records each file by stem.
    # Nested directories are not part of the root migration track.
    pending = [f.stem for f in sorted(migrations_dir.glob("*.sql")) if f.stem not in applied]
    if pending:
        report.findings.append(
            Finding(
                "warning",
                "pending_migrations",
                f"{len(pending)} migration(s) not applied: {', '.join(pending[:3])}{'...' if len(pending) > 3 else ''}",
            )
        )


def _check_enforcement_rules(report: WorkspaceReport, db_path: Path) -> None:
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, active FROM behaviors WHERE workspace_id = ? AND name LIKE 'pretooluse%'",
            (report.workspace_id,),
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return  # pre-migration DB; orthogonal check
    found = {r["name"] for r in rows if r["active"]}
    missing = _EXPECTED_PRETOOLUSE_RULES - found
    if missing:
        report.findings.append(
            Finding(
                "warning",
                "missing_enforcement_rule",
                f"{len(missing)} pretooluse:* rule(s) not active: {', '.join(sorted(missing))}",
            )
        )


def run_workspace_check(entry: dict[str, object], *, migrations_dir: Path) -> WorkspaceReport:
    """Diagnose one workspace registry entry. Returns a structured report."""
    report = WorkspaceReport(
        workspace_id=str(entry.get("id") or entry.get("workspace_id") or "<unknown>"),
        project_root=str(entry.get("project_root") or ""),
    )
    _check_paths(report, entry)
    project_root = Path(report.project_root)
    if project_root.exists():
        _check_settings_files(report, project_root)
        _check_agent_contract_files(report, project_root)
    db_raw = entry.get("db_path")
    db_path = Path(db_raw if isinstance(db_raw, str) else "")
    if db_path.exists():
        _check_migrations(report, db_path, migrations_dir)
        _check_enforcement_rules(report, db_path)
    if not report.findings:
        report.findings.append(Finding("ok", "clean", "no drift detected"))
    return report


def run_all(registry_path: Path, *, migrations_dir: Path) -> list[WorkspaceReport]:
    """Diagnose every workspace in the registry."""
    if not registry_path.exists():
        return []
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    entries = data.get("workspaces") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [run_workspace_check(e, migrations_dir=migrations_dir) for e in entries]


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file into a flat dict; comments and blank lines ignored."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value.strip()
    return out


def _manifest_workspace(db_path: Path) -> str | None:
    """Read the owning workspace_id out of a DB's workspace_manifest row."""
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT workspace_id FROM workspace_manifest WHERE id = 1").fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    return str(row[0]) if row and row[0] else None


def check_env_http_workspace(repo_root: Path) -> WorkspaceReport:
    """Check the repo's own .env against the DB manifest it points at.

    The HTTP service (``python -m agent_memory_lite``) bootstraps its
    workspace from ``.env``'s ``MEMORY_WORKSPACE_ID``; the DB manifest is
    stamped from the MCP / setup workspace. When they drift the service
    raises ``WorkspaceManifestError`` on startup and never binds. setup
    only ever wrote the workspace into ``.claude/settings.json`` (the MCP
    path), so the drift stayed invisible until the operator tried to
    start the service. Surfaced as its own report row -- it is a single
    file, not a per-registry workspace.
    """
    report = WorkspaceReport(workspace_id="<HTTP service .env>", project_root=str(repo_root))
    env_path = repo_root / ".env"
    if not env_path.exists():
        report.findings.append(
            Finding("ok", "clean", ".env absent - HTTP service uses built-in settings defaults")
        )
        return report
    env = _parse_env_file(env_path)
    env_ws = env.get("MEMORY_WORKSPACE_ID", "default")
    db_raw = env.get("MEMORY_DB_PATH", "./.agent_memory/memory.db")
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = (repo_root / db_path).resolve()
    if not db_path.exists():
        report.findings.append(
            Finding("ok", "clean", f".env workspace={env_ws!r}; DB not created yet")
        )
        return report
    manifest_ws = _manifest_workspace(db_path)
    if manifest_ws is None:
        report.findings.append(
            Finding("ok", "clean", f".env workspace={env_ws!r}; DB has no manifest row")
        )
    elif manifest_ws != env_ws:
        report.findings.append(
            Finding(
                "critical",
                "env_workspace_manifest_mismatch",
                f".env MEMORY_WORKSPACE_ID={env_ws!r} != DB manifest {manifest_ws!r} -- "
                f"'python -m agent_memory_lite' will crash with WorkspaceManifestError. "
                f"Re-run setup_agent.py to align .env (or set MEMORY_WORKSPACE_ID={manifest_ws!r}).",
            )
        )
    else:
        report.findings.append(
            Finding("ok", "clean", f".env workspace={env_ws!r} matches DB manifest")
        )
    return report


_SEVERITY_GLYPH = {"ok": "OK ", "warning": "WARN", "critical": "FAIL"}


def render_text(reports: list[WorkspaceReport]) -> str:
    """Pretty plain-text rendering for stdout. ASCII-only for cmd.exe."""
    if not reports:
        return "No workspaces registered. Run setup_agent.py --project <path> first.\n"
    lines = ["", "setup_agent.py --doctor — workspace drift report", "=" * 64]
    for r in reports:
        lines.append(
            f"\n[{_SEVERITY_GLYPH[r.status]}] workspace={r.workspace_id!r}  root={r.project_root}"
        )
        for f in r.findings:
            lines.append(f"     [{_SEVERITY_GLYPH[f.severity]}] {f.code}: {f.detail}")
    summary = {"ok": 0, "warning": 0, "critical": 0}
    for r in reports:
        summary[r.status] += 1
    lines.append("")
    lines.append(
        f"Summary: ok={summary['ok']}  warning={summary['warning']}  critical={summary['critical']}"
    )
    return "\n".join(lines) + "\n"


def render_json(reports: list[WorkspaceReport]) -> str:
    return json.dumps(
        {
            "workspaces": [
                {
                    "workspace_id": r.workspace_id,
                    "project_root": r.project_root,
                    "status": r.status,
                    "findings": [
                        {"severity": f.severity, "code": f.code, "detail": f.detail}
                        for f in r.findings
                    ],
                }
                for r in reports
            ],
            "summary": {
                "ok": sum(1 for r in reports if r.status == "ok"),
                "warning": sum(1 for r in reports if r.status == "warning"),
                "critical": sum(1 for r in reports if r.status == "critical"),
            },
        },
        indent=2,
    )


def exit_code_for(reports: list[WorkspaceReport]) -> int:
    """Convention: 0 healthy, 1 warnings only, 2 any critical."""
    if any(r.status == "critical" for r in reports):
        return 2
    if any(r.status == "warning" for r in reports):
        return 1
    return 0


def main(*, as_json: bool = False) -> int:
    registry_path = Path(
        os.environ.get("MEMORY_WORKSPACES_FILE")
        or (Path.home() / ".agent_memory" / "workspaces.json")
    )
    repo_root = Path(__file__).resolve().parents[1]
    migrations_dir = repo_root / "migrations"
    reports = [
        check_env_http_workspace(repo_root),
        *run_all(registry_path, migrations_dir=migrations_dir),
    ]
    print((render_json if as_json else render_text)(reports))
    return exit_code_for(reports)


if __name__ == "__main__":
    sys.exit(main(as_json="--json" in sys.argv))
