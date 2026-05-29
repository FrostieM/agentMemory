"""Inspect and explicitly quarantine cross-workspace rows.

Default mode is read-only. Quarantine mode exports offending rows to JSON,
requires --backup-first, then deletes them from the selected database.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.maintenance.backup_retention import DEFAULT_KEEP, prune_backups
from agent_memory_lite.maintenance.workspace_doctor import run_workspace_doctor
from agent_memory_lite.utils.time import iso_now


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect workspace isolation in memory.db.")
    parser.add_argument("--workspace", default=None, help="Expected workspace id.")
    parser.add_argument("--db-path", default=None, help="SQLite memory.db path.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--include-default",
        action="store_true",
        help="Treat default workspace rows as quarantinable pollution too.",
    )
    parser.add_argument(
        "--quarantine",
        action="store_true",
        help="Export matching rows to JSON and delete them from memory.db.",
    )
    parser.add_argument(
        "--backup-first",
        action="store_true",
        help="Copy memory.db under .agent_memory/backups before quarantine.",
    )
    parser.add_argument(
        "--quarantine-path",
        default=None,
        help="JSON path for exported rows. Defaults to .agent_memory/backups/...",
    )
    parser.add_argument("--sample-limit", type=int, default=5)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)
    updates: dict[str, Any] = {}
    if args.workspace:
        updates["workspace_id"] = args.workspace
    if args.db_path:
        updates["db_path"] = Path(args.db_path)
    return settings.model_copy(update=updates)


def _stamp() -> str:
    return iso_now().replace(":", "").replace("-", "").replace("+", "Z")


def _backup(db_path: Path) -> str:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"memory_before_workspace_doctor_{_stamp()}.db"
    shutil.copy2(db_path, target)
    prune_backups(
        backup_dir, prefix="memory_before_workspace_doctor_", keep=DEFAULT_KEEP, protect=target
    )
    return str(target)


def _quarantine_path(settings: Settings, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    backup_dir = settings.db_path.parent / "backups"
    return backup_dir / f"workspace_pollution_quarantine_{_stamp()}.json"


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    if payload["counts_before"]:
        print("counts_before=" + json.dumps(payload["counts_before"], ensure_ascii=False))
    if payload["quarantined_rows"]:
        print("quarantined_rows=" + json.dumps(payload["quarantined_rows"], ensure_ascii=False))
    if payload.get("backup_path"):
        print(f"backup_path={payload['backup_path']}")
    if payload.get("quarantine_path"):
        print(f"quarantine_path={payload['quarantine_path']}")
    for sample in payload["samples"]:
        print(
            "sample="
            + json.dumps(
                {
                    "table": sample["table"],
                    "workspace_id": sample["workspace_id"],
                    "row_identity": sample["row_identity"],
                },
                ensure_ascii=False,
            )
        )


def main() -> int:
    args = _parser().parse_args()
    if args.quarantine and not args.backup_first:
        print("Refusing quarantine without --backup-first.", file=sys.stderr)
        return 1

    settings = _settings(args)
    conn = open_connection(settings.db_path)
    try:
        backup_path: str | None = None
        if args.quarantine:
            backup_path = _backup(settings.db_path)
        report = run_workspace_doctor(
            conn,
            workspace_id=settings.workspace_id,
            include_default=args.include_default,
            quarantine=args.quarantine,
            quarantine_path=_quarantine_path(settings, args.quarantine_path)
            if args.quarantine
            else None,
            sample_limit=args.sample_limit,
        )
        payload = report.to_dict()
        if backup_path:
            payload["backup_path"] = backup_path
        _print(payload, as_json=args.json)
    except sqlite3.Error as exc:
        print(f"memory_workspace_doctor_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_connection(conn)
    return 2 if report.status == "degraded" else 0


if __name__ == "__main__":
    raise SystemExit(main())
