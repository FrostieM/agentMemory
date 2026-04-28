"""Fail a deploy/CI pipeline when memory integrity is not trustworthy."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run memory_audit.py as a strict gate.")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--vector-path", default=None)
    parser.add_argument("--allow-warnings", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
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

    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if completed.returncode not in {0, 2}:
        sys.stderr.write(completed.stderr)
        sys.stdout.write(completed.stdout)
        return 1
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(completed.stderr)
        sys.stdout.write(completed.stdout)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    status = str(payload.get("status"))
    warnings = list(payload.get("warnings", []))
    failures = list(payload.get("failures", []))
    if status == "degraded" or failures:
        print("memory_ci_gate_failed: degraded retrieval integrity", file=sys.stderr)
        return 2
    if warnings and not args.allow_warnings:
        print("memory_ci_gate_failed: warnings require review", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
