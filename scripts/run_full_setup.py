#!/usr/bin/env python3
"""One-shot onboarding wrapper: bootstrap_db -> ingest_workspace -> memory_audit.

Removes the need to remember the individual scripts when standing up a fresh
project. Each stage is a thin subprocess call to the existing script; failures
short-circuit the chain. The memory_audit `degraded` status (exit 2) is
surfaced as a warning rather than a hard failure.

Examples:

    python scripts/run_full_setup.py --path .
    python scripts/run_full_setup.py --path . --workspace myproj --json
    python scripts/run_full_setup.py --path . --skip-bootstrap
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
BOOTSTRAP_SCRIPT = SCRIPTS_DIR / "bootstrap_db.py"
INGEST_SCRIPT = SCRIPTS_DIR / "ingest_workspace.py"
AUDIT_SCRIPT = SCRIPTS_DIR / "memory_audit.py"

AUDIT_DEGRADED_EXIT = 2
TOTAL_STAGES = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run bootstrap_db, ingest_workspace, and memory_audit in sequence."
    )
    parser.add_argument("--path", required=True, help="Workspace root path to ingest.")
    parser.add_argument("--workspace", default=None, help="workspace_id (defaults to settings).")
    parser.add_argument("--db-path", default=None, help="Override SQLite memory.db path.")
    parser.add_argument("--vector-path", default=None, help="Override vector store path.")
    parser.add_argument("--skip-bootstrap", action="store_true", help="Skip DB migrations stage.")
    parser.add_argument(
        "--skip-ingest", action="store_true", help="Skip workspace ingestion stage."
    )
    parser.add_argument("--skip-audit", action="store_true", help="Skip integrity audit stage.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    return parser


def _env_with_overrides(args: argparse.Namespace) -> dict[str, str]:
    env = dict(os.environ)
    if args.workspace:
        env["MEMORY_WORKSPACE_ID"] = args.workspace
    if args.db_path:
        env["MEMORY_DB_PATH"] = args.db_path
    if args.vector_path:
        env["VECTOR_DB_PATH"] = args.vector_path
    return env


def _run(cmd: list[str], env: dict[str, str], capture: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, check=False, text=True, capture_output=capture)


def _print_human(stage_no: int, name: str, status: str, detail: str = "") -> None:
    label = f"[{stage_no}/{TOTAL_STAGES}] {name}".ljust(32, ".")
    suffix = f" {detail}" if detail else ""
    print(f"{label} {status}{suffix}")


def _emit_failure(
    stage: dict[str, Any], proc: subprocess.CompletedProcess[str], as_json: bool
) -> None:
    if as_json:
        stage["stderr"] = proc.stderr or ""
        stage["stdout"] = proc.stdout or ""
        return
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.stdout:
        print(proc.stdout, file=sys.stderr)


def _stage_skipped(stage_no: int, name: str, as_json: bool) -> dict[str, Any]:
    if not as_json:
        _print_human(stage_no, name, "skipped")
    return {"name": name, "status": "skipped", "exit_code": None, "skipped": True}


def _run_bootstrap(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    if args.skip_bootstrap:
        return _stage_skipped(1, "bootstrap_db", args.json)
    proc = _run([sys.executable, str(BOOTSTRAP_SCRIPT)], env=env, capture=args.json)
    if proc.returncode != 0:
        stage: dict[str, Any] = {
            "name": "bootstrap_db",
            "status": "failed",
            "exit_code": proc.returncode,
            "skipped": False,
        }
        _emit_failure(stage, proc, args.json)
        return stage
    if not args.json:
        _print_human(1, "bootstrap_db", "ok")
    return {"name": "bootstrap_db", "status": "ok", "exit_code": 0, "skipped": False}


def _run_ingest(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    if args.skip_ingest:
        return _stage_skipped(2, "ingest_workspace", args.json)
    cmd = [sys.executable, str(INGEST_SCRIPT), "--path", args.path]
    if args.workspace:
        cmd += ["--workspace", args.workspace]
    proc = _run(cmd, env=env, capture=args.json)
    if proc.returncode != 0:
        stage: dict[str, Any] = {
            "name": "ingest_workspace",
            "status": "failed",
            "exit_code": proc.returncode,
            "skipped": False,
        }
        _emit_failure(stage, proc, args.json)
        return stage
    if not args.json:
        _print_human(2, "ingest_workspace", "ok")
    return {"name": "ingest_workspace", "status": "ok", "exit_code": 0, "skipped": False}


def _run_audit(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    if args.skip_audit:
        return _stage_skipped(3, "memory_audit", args.json)
    cmd = [sys.executable, str(AUDIT_SCRIPT)]
    if args.workspace:
        cmd += ["--workspace", args.workspace]
    if args.db_path:
        cmd += ["--db-path", args.db_path]
    if args.vector_path:
        cmd += ["--vector-path", args.vector_path]
    cmd.append("--json")
    proc = _run(cmd, env=env, capture=True)
    report: dict[str, Any] | None = None
    if proc.stdout:
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            report = None
    if proc.returncode not in (0, AUDIT_DEGRADED_EXIT):
        stage: dict[str, Any] = {
            "name": "memory_audit",
            "status": "failed",
            "exit_code": proc.returncode,
            "skipped": False,
            "report": report,
        }
        _emit_failure(stage, proc, args.json)
        return stage
    audit_status = (report or {}).get("status") or (
        "degraded" if proc.returncode == AUDIT_DEGRADED_EXIT else "ok"
    )
    if not args.json:
        _print_human(3, "memory_audit", "ok", detail=f"(status={audit_status})")
    return {
        "name": "memory_audit",
        "status": audit_status,
        "exit_code": proc.returncode,
        "skipped": False,
        "report": report,
    }


def _finish(stages: list[dict[str, Any]], as_json: bool, overall: str) -> int:
    if as_json:
        print(json.dumps({"stages": stages, "overall_status": overall}, indent=2, sort_keys=False))
    elif overall == "failed":
        failed = next((s["name"] for s in stages if s["status"] == "failed"), "?")
        print(f"\nSetup failed at stage: {failed}", file=sys.stderr)
    else:
        print("\nNext: start the HTTP service with `python scripts/serve.py`.")
    return 1 if overall == "failed" else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = _env_with_overrides(args)
    stages: list[dict[str, Any]] = []

    for runner in (_run_bootstrap, _run_ingest, _run_audit):
        stage = runner(args, env)
        stages.append(stage)
        if stage["status"] == "failed":
            return _finish(stages, args.json, overall="failed")

    overall = "degraded" if any(s.get("status") == "degraded" for s in stages) else "ok"
    return _finish(stages, args.json, overall=overall)


if __name__ == "__main__":
    sys.exit(main())
