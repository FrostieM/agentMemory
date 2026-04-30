"""One-command trust dashboard for a project memory database."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.sentinels import discover_sentinel_file


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run memory integrity, hygiene, MCP, candidate, and contract checks."
    )
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--vector-path", "--vectors", dest="vector_path", default=None)
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--sentinels", default=None)
    parser.add_argument(
        "--require-sentinels",
        action="store_true",
        help="Treat missing project retrieval sentinels as a trust failure.",
    )
    parser.add_argument("--allow-project-name", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-vector", action="store_true")
    parser.add_argument("--skip-mcp", action="store_true")
    parser.add_argument("--skip-contract", action="store_true")
    parser.add_argument("--skip-candidates", action="store_true")
    parser.add_argument("--skip-restore-check", action="store_true")
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)
    updates: dict[str, Any] = {}
    if args.workspace:
        updates["workspace_id"] = args.workspace
    if args.db_path:
        updates["db_path"] = Path(args.db_path)
    if args.vector_path:
        updates["vector_db_path"] = Path(args.vector_path)
    return settings.model_copy(update=updates)


def _script(name: str) -> str:
    return str(Path(__file__).with_name(name))


def _run_json(name: str, cmd: list[str], *, ok_codes: set[int]) -> dict[str, Any]:
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "status": "degraded",
            "failures": [f"{name} did not emit JSON"],
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    if completed.returncode not in ok_codes and not payload.get("failures"):
        payload.setdefault("failures", []).append(f"{name} exited {completed.returncode}")
    if completed.stderr.strip():
        payload.setdefault("stderr", completed.stderr.strip())
    payload.setdefault("status", "degraded" if completed.returncode not in ok_codes else "ok")
    payload["exit_code"] = completed.returncode
    return payload


def _component_status(component: dict[str, Any]) -> str:
    status = str(component.get("status", "unknown"))
    if status in {"ok", "warning", "degraded", "unknown"}:
        return status
    return "degraded"


def _combine_status(components: dict[str, dict[str, Any]]) -> str:
    statuses = [_component_status(component) for component in components.values()]
    if "degraded" in statuses:
        return "degraded"
    if "warning" in statuses:
        return "warning"
    if all(status == "unknown" for status in statuses):
        return "unknown"
    return "ok"


def _base_args(settings: Settings) -> list[str]:
    return [
        "--workspace",
        settings.workspace_id,
        "--db-path",
        str(settings.db_path),
    ]


def run_dashboard(args: argparse.Namespace) -> dict[str, Any]:  # noqa: PLR0912
    settings = _settings(args)
    components: dict[str, dict[str, Any]] = {}
    project_root = Path(args.project_root).resolve() if args.project_root else None
    sentinel_discovery = discover_sentinel_file(
        explicit_path=args.sentinels,
        db_path=settings.db_path,
        project_root=project_root,
        require=args.require_sentinels,
    )

    audit_cmd = [
        sys.executable,
        _script("memory_audit.py"),
        *_base_args(settings),
        "--vector-path",
        str(settings.vector_db_path),
        "--json",
    ]
    components["integrity"] = _run_json("memory_audit", audit_cmd, ok_codes={0, 2})

    workspace_doctor_cmd = [
        sys.executable,
        _script("memory_workspace_doctor.py"),
        *_base_args(settings),
        "--json",
    ]
    components["workspace_doctor"] = _run_json(
        "memory_workspace_doctor",
        workspace_doctor_cmd,
        ok_codes={0, 2},
    )

    hygiene_cmd = [
        sys.executable,
        _script("memory_hygiene.py"),
        *_base_args(settings),
        "--json",
    ]
    components["hygiene"] = _run_json("memory_hygiene", hygiene_cmd, ok_codes={0, 2})

    encoding_cmd = [
        sys.executable,
        _script("memory_encoding_audit.py"),
        *_base_args(settings),
        "--json",
    ]
    components["encoding"] = _run_json("memory_encoding_audit", encoding_cmd, ok_codes={0, 2})

    watchdog_cmd = [
        sys.executable,
        _script("memory_watchdog.py"),
        "--workspace-id",
        settings.workspace_id,
        "--db",
        str(settings.db_path),
        "--vectors",
        str(settings.vector_db_path),
        "--json",
        "--no-artifact",
        "--no-maintenance-event",
    ]
    if args.no_vector:
        watchdog_cmd.append("--no-vector")
    if sentinel_discovery.path is not None:
        watchdog_cmd.extend(["--sentinels", str(sentinel_discovery.path)])
    if args.require_sentinels:
        watchdog_cmd.append("--require-sentinels")
    components["watchdog"] = _run_json("memory_watchdog", watchdog_cmd, ok_codes={0, 2})
    components["watchdog"]["sentinels_discovered"] = sentinel_discovery.to_dict()
    if (
        components["watchdog"].get("retrieval_eval", {}).get("status") == "unknown"
        and sentinel_discovery.path is None
    ):
        components["watchdog"].setdefault("warnings", []).append(
            "retrieval sentinel evals were not run; add .agent_memory/retrieval_sentinels.yaml"
        )
        components["watchdog"]["status"] = "warning"
    if sentinel_discovery.warnings:
        components["watchdog"].setdefault("failures", []).extend(sentinel_discovery.warnings)
        components["watchdog"]["status"] = "degraded"

    if not args.skip_mcp:
        mcp_cmd = [
            sys.executable,
            _script("memory_mcp_smoke.py"),
            "--workspace",
            settings.workspace_id,
            "--db-path",
            str(settings.db_path),
            "--vector-path",
            str(settings.vector_db_path),
            "--require-behavior",
            "--require-capabilities",
            "--json",
        ]
        components["mcp_smoke"] = _run_json("memory_mcp_smoke", mcp_cmd, ok_codes={0, 2})

    if not args.skip_candidates:
        candidate_cmd = [
            sys.executable,
            _script("memory_candidate_triage.py"),
            "--workspace",
            settings.workspace_id,
            "--db-path",
            str(settings.db_path),
            "--json",
        ]
        components["candidate_triage"] = _run_json(
            "memory_candidate_triage",
            candidate_cmd,
            ok_codes={0, 2},
        )

    if not args.skip_restore_check:
        restore_cmd = [
            sys.executable,
            _script("memory_backup_restore_check.py"),
            "--workspace",
            settings.workspace_id,
            "--db-path",
            str(settings.db_path),
            "--vector-path",
            str(settings.vector_db_path),
            "--json",
        ]
        components["backup_restore"] = _run_json(
            "memory_backup_restore_check",
            restore_cmd,
            ok_codes={0, 2},
        )

    trend_cmd = [
        sys.executable,
        _script("memory_trend_report.py"),
        "--db-path",
        str(settings.db_path),
        "--json",
    ]
    components["trend"] = _run_json("memory_trend_report", trend_cmd, ok_codes={0, 2})

    if project_root is not None and not args.skip_contract:
        contract_cmd = [
            sys.executable,
            _script("memory_contract_check.py"),
            "--root",
            str(project_root),
            "--workspace",
            settings.workspace_id,
            "--json",
        ]
        allowed_project_names = {
            settings.workspace_id,
            project_root.name,
            *{str(project_name) for project_name in args.allow_project_name},
        }
        for project_name in sorted(item for item in allowed_project_names if item):
            contract_cmd.extend(["--allow-project-name", str(project_name)])
        components["contract"] = _run_json("memory_contract_check", contract_cmd, ok_codes={0})

    status = _combine_status(components)
    failures: list[str] = []
    warnings: list[str] = []
    for name, component in components.items():
        for failure in component.get("failures", []):
            failures.append(f"{name}: {failure}")
        for warning in component.get("warnings", []):
            warnings.append(f"{name}: {warning}")
    return {
        "status": status,
        "workspace_id": settings.workspace_id,
        "db_path": str(settings.db_path),
        "vector_path": str(settings.vector_db_path),
        "sentinels": sentinel_discovery.to_dict(),
        "components": components,
        "failures": failures,
        "warnings": warnings,
    }


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    for name, component in payload["components"].items():
        print(f"{name}: {component.get('status')} exit={component.get('exit_code')}")
    if payload["failures"]:
        print("failures=" + json.dumps(payload["failures"], ensure_ascii=False))
    if payload["warnings"]:
        print("warnings=" + json.dumps(payload["warnings"], ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = run_dashboard(args)
    _print(payload, as_json=args.json)
    return 2 if payload["status"] == "degraded" else 0


if __name__ == "__main__":
    raise SystemExit(main())
