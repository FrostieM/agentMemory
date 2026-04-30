"""Fail a deploy/CI pipeline when memory integrity is not trustworthy."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from agent_memory_lite.maintenance.sentinels import discover_sentinel_file


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run memory_audit.py as a strict gate.")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--vector-path", default=None)
    parser.add_argument("--allow-warnings", action="store_true")
    parser.add_argument("--sentinels", default=None)
    parser.add_argument("--require-sentinels", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    return parser


def _run_json(cmd: list[str]) -> tuple[int, dict[str, object], str]:
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "status": "degraded",
            "failures": ["command did not emit JSON"],
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    return completed.returncode, payload, completed.stderr


def _audit_command(args: argparse.Namespace) -> list[str]:
    audit_script = Path(__file__).with_name("memory_audit.py")
    cmd = [sys.executable, str(audit_script), "--json"]
    if args.workspace:
        cmd.extend(["--workspace", args.workspace])
    if args.db_path:
        cmd.extend(["--db-path", args.db_path])
    if args.vector_path:
        cmd.extend(["--vector-path", args.vector_path])
    if args.migrate:
        cmd.append("--migrate")
    return cmd


def _watchdog_command(args: argparse.Namespace, sentinel_path: Path) -> list[str]:
    watchdog_script = Path(__file__).with_name("memory_watchdog.py")
    cmd = [
        sys.executable,
        str(watchdog_script),
        "--json",
        "--no-artifact",
        "--no-maintenance-event",
        "--sentinels",
        str(sentinel_path),
    ]
    if args.workspace:
        cmd.extend(["--workspace-id", args.workspace])
    if args.db_path:
        cmd.extend(["--db", args.db_path])
    if args.vector_path:
        cmd.extend(["--vectors", args.vector_path])
    return cmd


def _collect_watchdog_findings(
    args: argparse.Namespace,
    report: dict[str, object],
    warnings: list[object],
    failures: list[object],
) -> int:
    db_path = Path(args.db_path).resolve() if args.db_path else None
    sentinel_discovery = discover_sentinel_file(
        explicit_path=args.sentinels,
        db_path=db_path,
        require=args.require_sentinels or not args.allow_warnings,
    )
    report["sentinels"] = sentinel_discovery.to_dict()
    if sentinel_discovery.path is None:
        warnings.extend(sentinel_discovery.warnings or ["no retrieval sentinel file found"])
        return 0

    watchdog_code, watchdog_payload, watchdog_stderr = _run_json(
        _watchdog_command(args, sentinel_discovery.path)
    )
    if watchdog_code not in {0, 2}:
        sys.stderr.write(watchdog_stderr)
        return 1
    report["watchdog"] = watchdog_payload
    eval_payload = watchdog_payload.get("retrieval_eval", {})
    if isinstance(eval_payload, dict) and eval_payload.get("status") == "degraded":
        failures.extend(
            f"retrieval_eval: {item}"
            for item in eval_payload.get("failures", [])
            if isinstance(item, str)
        )
    if isinstance(eval_payload, dict) and eval_payload.get("status") == "unknown":
        warnings.append("retrieval_eval did not run any sentinel cases")
    return 0


def main() -> int:
    args = _parser().parse_args()
    audit_code, payload, stderr = _run_json(_audit_command(args))
    if audit_code not in {0, 2}:
        sys.stderr.write(stderr)
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    report: dict[str, object] = {"status": payload.get("status"), "integrity": payload}
    warnings = (
        list(payload.get("warnings", [])) if isinstance(payload.get("warnings"), list) else []
    )
    failures = (
        list(payload.get("failures", [])) if isinstance(payload.get("failures"), list) else []
    )

    watchdog_error = _collect_watchdog_findings(args, report, warnings, failures)
    if watchdog_error:
        return watchdog_error

    report["warnings"] = warnings
    report["failures"] = failures
    if failures:
        report["status"] = "degraded"
    elif warnings:
        report["status"] = "warning"
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    status = str(payload.get("status"))
    if status == "degraded" or failures:
        print("memory_ci_gate_failed: degraded retrieval integrity", file=sys.stderr)
        return 2
    if warnings and not args.allow_warnings:
        print("memory_ci_gate_failed: warnings require review", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
