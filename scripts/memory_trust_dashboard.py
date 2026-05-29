"""One-command trust dashboard for a project memory database."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, cast

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.sentinels import discover_sentinel_file

DEFAULT_COMPONENT_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_PARALLEL = 4


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
    parser.add_argument(
        "--component-timeout-seconds",
        type=float,
        default=DEFAULT_COMPONENT_TIMEOUT_SECONDS,
        help="Maximum seconds to wait for each child check before marking it degraded.",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=DEFAULT_MAX_PARALLEL,
        help=(
            "Max component checks to run concurrently (each is an isolated child "
            "process). 1 = sequential. Bounded to avoid fanning out many "
            "model-loading children at once."
        ),
    )
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


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        proc.kill()


def _run_json(
    name: str,
    cmd: list[str],
    *,
    ok_codes: set[int],
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=max(timeout_seconds, 0.1))
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc)
        stdout, stderr = proc.communicate()
        return {
            "status": "degraded",
            "failures": [f"{name} timed out after {timeout_seconds:.1f}s"],
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
            "exit_code": None,
            "timed_out": True,
            "elapsed_sec": round(time.perf_counter() - started, 3),
        }
    try:
        payload = cast(dict[str, Any], json.loads(stdout))
    except json.JSONDecodeError:
        payload = {
            "status": "degraded",
            "failures": [f"{name} did not emit JSON"],
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        }
    if proc.returncode not in ok_codes and not payload.get("failures"):
        payload.setdefault("failures", []).append(f"{name} exited {proc.returncode}")
    if stderr.strip():
        payload.setdefault("stderr", stderr.strip())
    payload.setdefault("status", "degraded" if proc.returncode not in ok_codes else "ok")
    payload["exit_code"] = proc.returncode
    payload["elapsed_sec"] = round(time.perf_counter() - started, 3)
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


def _component_specs(
    args: argparse.Namespace,
    settings: Settings,
    sentinel_discovery: Any,
    project_root: Path | None,
) -> list[tuple[str, list[str], set[int]]]:
    """Build the (name, cmd, ok_codes) spec for every dashboard component that
    should run, honoring the --skip-* flags. Each component is an independent
    child process, so the caller can run them concurrently."""
    base = _base_args(settings)
    specs: list[tuple[str, list[str], set[int]]] = [
        (
            "integrity",
            [
                sys.executable,
                _script("memory_audit.py"),
                *base,
                "--vector-path",
                str(settings.vector_db_path),
                "--json",
            ],
            {0, 2},
        ),
        (
            "workspace_doctor",
            [sys.executable, _script("memory_workspace_doctor.py"), *base, "--json"],
            {0, 2},
        ),
        ("hygiene", [sys.executable, _script("memory_hygiene.py"), *base, "--json"], {0, 2}),
        (
            "feedback",
            [sys.executable, _script("memory_feedback_report.py"), *base, "--json"],
            {0, 2},
        ),
        (
            "encoding",
            [sys.executable, _script("memory_encoding_audit.py"), *base, "--json"],
            {0, 2},
        ),
    ]
    if not args.skip_mcp:
        specs.append(
            (
                "mcp_smoke",
                [
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
                    "--max-seconds",
                    "8.0",
                    "--json",
                ],
                {0, 2},
            )
        )
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
    specs.append(("watchdog", watchdog_cmd, {0, 2}))
    if not args.skip_candidates:
        specs.append(
            (
                "candidate_triage",
                [
                    sys.executable,
                    _script("memory_candidate_triage.py"),
                    "--workspace",
                    settings.workspace_id,
                    "--db-path",
                    str(settings.db_path),
                    "--json",
                ],
                {0, 2},
            )
        )
    if not args.skip_restore_check:
        specs.append(
            (
                "backup_restore",
                [
                    sys.executable,
                    _script("memory_backup_restore_check.py"),
                    "--workspace",
                    settings.workspace_id,
                    "--db-path",
                    str(settings.db_path),
                    "--vector-path",
                    str(settings.vector_db_path),
                    "--audit-timeout-seconds",
                    str(args.component_timeout_seconds),
                    "--json",
                ],
                {0, 2},
            )
        )
    specs.append(
        (
            "trend",
            [
                sys.executable,
                _script("memory_trend_report.py"),
                "--db-path",
                str(settings.db_path),
                "--json",
            ],
            {0, 2},
        )
    )
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
        specs.append(("contract", contract_cmd, {0}))
    return specs


def _augment_watchdog(component: dict[str, Any], sentinel_discovery: Any) -> None:
    """Fold sentinel-discovery state into the watchdog component (unchanged from
    the sequential version, hoisted out so it runs after the parallel collect)."""
    component["sentinels_discovered"] = sentinel_discovery.to_dict()
    if (
        component.get("retrieval_eval", {}).get("status") == "unknown"
        and sentinel_discovery.path is None
    ):
        component.setdefault("warnings", []).append(
            "retrieval sentinel evals were not run; add .agent_memory/retrieval_sentinels.yaml"
        )
        component["status"] = "warning"
    if sentinel_discovery.warnings:
        component.setdefault("failures", []).extend(sentinel_discovery.warnings)
        component["status"] = "degraded"


def run_dashboard(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings(args)
    project_root = Path(args.project_root).resolve() if args.project_root else None
    sentinel_discovery = discover_sentinel_file(
        explicit_path=args.sentinels,
        db_path=settings.db_path,
        project_root=project_root,
        require=args.require_sentinels,
    )
    specs = _component_specs(args, settings, sentinel_discovery, project_root)

    # Each component is an independent child process (SQLite WAL tolerates the
    # single mcp_smoke writer alongside the read-only audits), so run them
    # concurrently to keep wall-clock near the slowest single check instead of
    # the sum of all checks. Bounded by --max-parallel so we never fan out many
    # model-loading children at once.
    max_workers = max(1, min(args.max_parallel, len(specs)))
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _run_json,
                # The script basename (e.g. "memory_audit") is the operator-
                # facing label in failure/timeout text; the component key is the
                # output dict key. cmd[1] is always the _script(...) path.
                Path(cmd[1]).stem,
                cmd,
                ok_codes=ok_codes,
                timeout_seconds=args.component_timeout_seconds,
            ): name
            for name, cmd, ok_codes in specs
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                # A child that could not even be spawned must not abort the
                # whole dashboard -- mark it degraded like a non-JSON result.
                results[name] = {
                    "status": "degraded",
                    "failures": [f"{name} failed to run: {exc}"],
                    "exit_code": None,
                }

    # Re-assemble in deterministic spec order (as_completed yields by finish
    # time, which would otherwise scramble the component order in plain output).
    components: dict[str, dict[str, Any]] = {name: results[name] for name, _, _ in specs}
    if "watchdog" in components:
        _augment_watchdog(components["watchdog"], sentinel_discovery)

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
