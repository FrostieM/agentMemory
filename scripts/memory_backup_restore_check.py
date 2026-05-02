"""Dry-run backup/restore drill for a memory DB and vector store."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy memory storage to temp and audit the copy.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--vector-path", "--vectors", dest="vector_path", default=None)
    parser.add_argument("--json", action="store_true")
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


def run_backup_restore_check(settings: Settings) -> dict[str, Any]:
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
        completed = subprocess.run(audit_cmd, check=False, capture_output=True, text=True)
        try:
            audit = json.loads(completed.stdout)
        except json.JSONDecodeError:
            audit = {
                "status": "degraded",
                "failures": ["restored audit did not emit JSON"],
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        status = "ok" if completed.returncode == 0 and audit.get("status") == "ok" else "degraded"
        return {
            "status": status,
            "workspace_id": settings.workspace_id,
            "source_db_path": str(settings.db_path),
            "source_vector_path": str(settings.vector_db_path),
            "copied_db_bytes": db_copy.stat().st_size if db_copy.exists() else 0,
            "vector_copy_exists": vector_copy.exists(),
            "audit": audit,
            "audit_exit_code": completed.returncode,
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
        payload = run_backup_restore_check(settings)
    except Exception as exc:
        print(f"memory_backup_restore_check_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    _print(payload, as_json=args.json)
    return 0 if payload["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
