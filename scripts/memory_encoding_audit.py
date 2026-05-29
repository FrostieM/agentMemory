"""Audit and explicitly repair stored mojibake in memory text columns."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.maintenance.backup_retention import DEFAULT_KEEP, prune_backups
from agent_memory_lite.maintenance.encoding_audit import run_encoding_audit
from agent_memory_lite.utils.time import iso_now


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit and repair memory text encoding.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--backup-first", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)
    updates: dict[str, Any] = {}
    if args.workspace:
        updates["workspace_id"] = args.workspace
    if args.db_path:
        updates["db_path"] = Path(args.db_path)
    return settings.model_copy(update=updates)


def _backup(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    stamp = iso_now().replace(":", "").replace("-", "").replace("+", "Z")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"memory_before_encoding_repair_{stamp}.db"
    shutil.copy2(db_path, target)
    prune_backups(
        backup_dir, prefix="memory_before_encoding_repair_", keep=DEFAULT_KEEP, protect=target
    )
    return str(target)


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    print(f"findings={payload['findings_count']} repaired={payload['repaired_cells']}")
    for finding in payload["findings"][:20]:
        print(
            f"{finding['issue']} {finding['table']}:{finding['row_id']} "
            f"{finding['column']} before={finding['before_sample']!r}"
        )
    if payload.get("backup_path"):
        print(f"backup_path={payload['backup_path']}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _settings(args)
    if args.repair and not args.backup_first:
        print("Refusing repair without --backup-first.", file=sys.stderr)
        return 1
    conn = open_connection(settings.db_path)
    try:
        if args.migrate:
            apply_migrations(conn)
        backup_path = _backup(settings.db_path) if args.repair else None
        report = run_encoding_audit(
            conn,
            workspace_id=settings.workspace_id,
            repair=args.repair,
            limit=args.limit,
        )
        payload = report.to_dict()
        if backup_path:
            payload["backup_path"] = backup_path
        _print(payload, as_json=args.json)
    except Exception as exc:
        print(f"memory_encoding_audit_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_connection(conn)
    return 0 if report.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
