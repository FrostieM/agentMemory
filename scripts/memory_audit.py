"""Audit and explicitly repair memory retrieval indexes.

Default mode is read-only and exits with 2 when retrieval integrity is degraded.
Repairs require explicit flags and should normally use --backup-first.
"""

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
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.maintenance.integrity import repair_fts, run_integrity_audit
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.factory import get_vector_store
from agent_memory_lite.vector_store.reindex import reindex_chunks


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit memory retrieval integrity.")
    parser.add_argument("--workspace", default=None, help="Workspace id to audit.")
    parser.add_argument("--db-path", default=None, help="SQLite memory.db path.")
    parser.add_argument("--vector-path", default=None, help="Vector store path.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--repair-fts", action="store_true", help="Rebuild chunks_fts from chunks.")
    parser.add_argument(
        "--repair-vectors",
        action="store_true",
        help="Drop and rebuild the chunks vector namespace from SQLite chunks.",
    )
    parser.add_argument(
        "--backup-first",
        action="store_true",
        help="Copy memory.db and vector store under .agent_memory/backups before repair.",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Apply pending schema migrations before audit. Default audit mode is read-only.",
    )
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    updates: dict[str, Any] = {}
    if args.workspace:
        updates["workspace_id"] = args.workspace
    if args.db_path:
        updates["db_path"] = Path(args.db_path)
    if args.vector_path:
        updates["vector_db_path"] = Path(args.vector_path)
    return settings.model_copy(update=updates)


def _backup(settings: Settings) -> dict[str, str]:
    stamp = iso_now().replace(":", "").replace("-", "").replace("+", "Z")
    backup_dir = settings.db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    if settings.db_path.exists():
        target = backup_dir / f"memory_before_audit_repair_{stamp}.db"
        shutil.copy2(settings.db_path, target)
        out["db"] = str(target)
    if settings.vector_db_path.exists():
        suffix = ".lance" if settings.vector_db_path.is_dir() else settings.vector_db_path.suffix
        target = backup_dir / f"vectors_before_audit_repair_{stamp}{suffix}"
        if settings.vector_db_path.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(settings.vector_db_path, target)
        else:
            shutil.copy2(settings.vector_db_path, target)
        out["vectors"] = str(target)
    return out


def _print(report: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={report['status']} workspace_id={report['workspace_id']}")
    if report["failures"]:
        print("failures=" + ",".join(report["failures"]))
    for name, check in report["checks"].items():
        print(f"{name}: {check['status']} {json.dumps(check['details'], ensure_ascii=False)}")
    for hint in report["repair_hints"]:
        print(f"repair_hint: {hint}")


def main() -> int:
    args = _parser().parse_args()
    settings = _settings(args)
    repairing = bool(args.repair_fts or args.repair_vectors)
    if repairing and not args.backup_first:
        print("Refusing repair without --backup-first.", file=sys.stderr)
        return 1

    conn = open_connection(settings.db_path)
    store = get_vector_store(settings)
    try:
        if args.migrate:
            apply_migrations(conn)
        backups: dict[str, str] = {}
        if args.backup_first and (repairing or args.migrate):
            backups = _backup(settings)
        if args.repair_fts:
            repair_fts(conn, workspace_id=settings.workspace_id)
        if args.repair_vectors:
            provider = get_embedding_provider(settings)
            reindex_chunks(
                conn,
                workspace_id=settings.workspace_id,
                provider=provider,
                store=store,
                batch_size=settings.embedding_batch_size,
            )
        report = run_integrity_audit(conn, workspace_id=settings.workspace_id, vector_store=store)
        payload = report.to_dict()
        if backups:
            payload["backups"] = backups
        _print(payload, as_json=args.json)
    except Exception as exc:
        print(f"memory_audit_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
        close_connection(conn)
    return 2 if report.status == "degraded" else 0


if __name__ == "__main__":
    raise SystemExit(main())
