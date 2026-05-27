"""Dry-run backup/restore drill for a memory DB and vector store."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings

DEFAULT_AUDIT_TIMEOUT_SECONDS = 180.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy memory storage to temp and audit the copy.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--vector-path", "--vectors", dest="vector_path", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--audit-timeout-seconds",
        type=float,
        default=DEFAULT_AUDIT_TIMEOUT_SECONDS,
        help="Maximum seconds to wait for the restored-copy audit.",
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


def _copy_vector_store(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
    elif source.exists():
        shutil.copy2(source, target)


def _backup_sqlite_db(source: Path, target: Path) -> None:
    """Copy a live SQLite DB safely, including committed WAL contents."""

    source_uri = f"file:{source.as_posix()}?mode=ro"
    with (
        closing(sqlite3.connect(source_uri, uri=True)) as source_conn,
        closing(sqlite3.connect(target)) as target_conn,
    ):
        source_conn.backup(target_conn)


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


def _run_restored_audit(
    audit_cmd: list[str],
    *,
    timeout_seconds: float,
) -> tuple[int | None, str, str, bool, float]:
    started = time.perf_counter()
    proc = subprocess.Popen(
        audit_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=max(timeout_seconds, 0.1))
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc)
        stdout, stderr = proc.communicate()
        return None, stdout, stderr, True, time.perf_counter() - started
    return proc.returncode, stdout, stderr, False, time.perf_counter() - started


def run_backup_restore_check(
    settings: Settings,
    *,
    audit_timeout_seconds: float = DEFAULT_AUDIT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="memory_restore_check_") as raw_tmp:
        tmp = Path(raw_tmp)
        db_copy = tmp / "memory.db"
        vector_copy = tmp / settings.vector_db_path.name
        _backup_sqlite_db(settings.db_path, db_copy)
        _copy_vector_store(settings.vector_db_path, vector_copy)
        audit_cmd = [
            sys.executable,
            str(Path(__file__).with_name("memory_audit.py")),
            "--workspace",
            settings.workspace_id,
            "--db-path",
            str(db_copy),
            "--vector-path",
            str(vector_copy),
            "--json",
        ]
        exit_code, stdout, stderr, timed_out, elapsed = _run_restored_audit(
            audit_cmd,
            timeout_seconds=audit_timeout_seconds,
        )
        if timed_out:
            return {
                "status": "degraded",
                "workspace_id": settings.workspace_id,
                "source_db_path": str(settings.db_path),
                "source_vector_path": str(settings.vector_db_path),
                "copied_db_bytes": db_copy.stat().st_size if db_copy.exists() else 0,
                "vector_copy_exists": vector_copy.exists(),
                "audit": {
                    "status": "degraded",
                    "failures": [f"restored audit timed out after {audit_timeout_seconds:.1f}s"],
                    "stdout": stdout.strip(),
                    "stderr": stderr.strip(),
                },
                "audit_exit_code": None,
                "audit_timed_out": True,
                "audit_elapsed_sec": round(elapsed, 3),
            }
        try:
            audit = json.loads(stdout)
        except json.JSONDecodeError:
            audit = {
                "status": "degraded",
                "failures": ["restored audit did not emit JSON"],
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
            }
        status = "ok" if exit_code == 0 and audit.get("status") == "ok" else "degraded"
        return {
            "status": status,
            "workspace_id": settings.workspace_id,
            "source_db_path": str(settings.db_path),
            "source_vector_path": str(settings.vector_db_path),
            "copied_db_bytes": db_copy.stat().st_size if db_copy.exists() else 0,
            "vector_copy_exists": vector_copy.exists(),
            "audit": audit,
            "audit_exit_code": exit_code,
            "audit_timed_out": False,
            "audit_elapsed_sec": round(elapsed, 3),
        }


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    print(f"copied_db_bytes={payload['copied_db_bytes']}")
    print(f"vector_copy_exists={payload['vector_copy_exists']}")
    print(f"audit_status={payload['audit'].get('status')} exit={payload['audit_exit_code']}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _settings(args)
    try:
        payload = run_backup_restore_check(
            settings,
            audit_timeout_seconds=args.audit_timeout_seconds,
        )
    except Exception as exc:
        print(f"memory_backup_restore_check_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    _print(payload, as_json=args.json)
    return 0 if payload["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
