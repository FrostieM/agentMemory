"""Audit and explicitly repair memory retrieval indexes.

Default mode is read-only and exits with 2 when retrieval integrity is degraded.
Repairs require explicit flags and should normally use --backup-first.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.maintenance.integrity import repair_fts, run_integrity_audit
from agent_memory_lite.repositories.vector_metadata_repo import provider_name_from_settings
from agent_memory_lite.repositories.workspace_manifest_repo import (
    ensure_workspace_manifest,
    update_workspace_manifest_repair,
)
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.factory import get_vector_store
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS
from agent_memory_lite.vector_store.reindex import reindex_chunks, repair_chunk_embedding_refs

# Force UTF-8 stdout/stderr — the audit's JSON / human output can carry
# non-ASCII memory content (e.g. → / ≤ glyphs from stored decisions or
# episodes) that crashes a default Windows cp1252 console with
# UnicodeEncodeError. Mirrors scripts/acceptance_gate.py.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
with contextlib.suppress(AttributeError, ValueError):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


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
        help=(
            "Resume-safe rebuild: embed only chunks missing from the vector store. "
            "v3.4+ default — safe to re-run, picks up where last run stopped."
        ),
    )
    parser.add_argument(
        "--rebuild-vectors-force",
        action="store_true",
        help=(
            "Force drop + full rebuild of the chunks vector namespace. Use only when "
            "the embedding model changed and stale vectors must go. Slower + risky if "
            "interrupted."
        ),
    )
    parser.add_argument(
        "--repair-embedding-refs",
        action="store_true",
        help=(
            "Backfill chunks.embedding_id and embedding text hashes from existing vector row ids "
            "without re-embedding."
        ),
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
    parser.add_argument(
        "--dry-run-repair",
        action="store_true",
        help="With repair flags, print the intended repair plan without mutating data.",
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
    if report.get("warnings"):
        print("warnings=" + ",".join(report["warnings"]))
    for name, check in report["checks"].items():
        print(f"{name}: {check['status']} {json.dumps(check['details'], ensure_ascii=False)}")
    for hint in report["repair_hints"]:
        print(f"repair_hint: {hint}")
    if report.get("repair_plan"):
        print("repair_plan=" + json.dumps(report["repair_plan"], ensure_ascii=False))


def _repair_plan(
    payload: dict[str, Any],
    *,
    repair_fts_flag: bool,
    repair_vectors_flag: bool,
    repair_embedding_refs_flag: bool,
) -> dict[str, Any]:
    checks = payload.get("checks", {})
    plan: dict[str, Any] = {"will_mutate": False, "steps": []}
    if repair_fts_flag:
        details = checks.get("fts", {}).get("details", {})
        plan["steps"].append(
            {
                "action": "rebuild_chunks_fts",
                "workspace_id": payload.get("workspace_id"),
                "expected_rows": details.get("chunks"),
                "current_rows": details.get("chunks_fts"),
                "missing": details.get("missing"),
                "extra": details.get("extra"),
                "workspace_mismatch": details.get("workspace_mismatch"),
            }
        )
    if repair_vectors_flag:
        details = checks.get("vector", {}).get("details", {})
        plan["steps"].append(
            {
                "action": "reindex_chunk_vectors",
                "workspace_id": payload.get("workspace_id"),
                "expected_rows": details.get("chunks"),
                "current_rows": details.get("vectors"),
                "missing": details.get("missing"),
                "extra": details.get("extra"),
                "missing_ids_sample": details.get("missing_ids_sample"),
                "extra_ids_sample": details.get("extra_ids_sample"),
            }
        )
    if repair_embedding_refs_flag:
        details = checks.get("vector", {}).get("details", {})
        plan["steps"].append(
            {
                "action": "repair_chunk_embedding_refs",
                "workspace_id": payload.get("workspace_id"),
                "missing_embedding_ids": details.get("missing_embedding_ids"),
                "missing_embedding_hashes": details.get("missing_embedding_hashes"),
                "expected_rows": details.get("chunks"),
                "current_rows": details.get("vectors"),
            }
        )
    return plan


def _chunk_count(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def main() -> int:  # noqa: PLR0912
    args = _parser().parse_args()
    settings = _settings(args)
    repairing = bool(
        args.repair_fts
        or args.repair_vectors
        or args.rebuild_vectors_force
        or args.repair_embedding_refs
    )
    if repairing and not args.backup_first and not args.dry_run_repair:
        print("Refusing repair without --backup-first.", file=sys.stderr)
        return 1

    conn = open_connection(settings.db_path)
    store = get_vector_store(settings)
    try:
        backups: dict[str, str] = {}
        if args.backup_first and (repairing or args.migrate) and not args.dry_run_repair:
            backups = _backup(settings)
        if args.migrate:
            apply_migrations(conn)
            if settings.enforce_workspace_manifest:
                ensure_workspace_manifest(
                    conn,
                    workspace_id=settings.workspace_id,
                    allow_default_workspace=not settings.forbid_default_workspace,
                )
        if args.repair_fts and not args.dry_run_repair:
            repair_fts(conn, workspace_id=settings.workspace_id)
        if (args.repair_vectors or args.rebuild_vectors_force) and not args.dry_run_repair:
            if _chunk_count(conn, workspace_id=settings.workspace_id) == 0:
                store.open()
                store.drop_namespace(NAMESPACE_CHUNKS)
            else:
                provider = get_embedding_provider(settings)
                # Progress callback: print one line per 5% milestone so the
                # operator sees movement during long rebuilds. Silent when
                # ``--json`` so we don't corrupt machine-readable output.
                last_pct: list[int] = [-1]

                def _progress(done: int, total: int) -> None:
                    if total == 0:
                        return
                    pct = int(100 * done / total)
                    if pct >= last_pct[0] + 5 and not args.json:
                        print(
                            f"  reindex_chunks: {done}/{total} ({pct}%)",
                            flush=True,
                        )
                        last_pct[0] = pct

                reindex_chunks(
                    conn,
                    workspace_id=settings.workspace_id,
                    provider=provider,
                    store=store,
                    batch_size=settings.embedding_batch_size,
                    resume=not args.rebuild_vectors_force,
                    progress_callback=_progress,
                )
        if args.repair_embedding_refs and not args.dry_run_repair:
            repair_chunk_embedding_refs(
                conn,
                workspace_id=settings.workspace_id,
                store=store,
            )
        if repairing and not args.dry_run_repair:
            update_workspace_manifest_repair(conn)
        report = run_integrity_audit(
            conn,
            workspace_id=settings.workspace_id,
            vector_store=store,
            db_path=settings.db_path,
            expected_provider_name=provider_name_from_settings(
                embedding_backend=settings.embedding_backend,
                embedding_model=settings.embedding_model,
            ),
            expected_vector_backend=settings.vector_backend,
        )
        payload = report.to_dict()
        if backups:
            payload["backups"] = backups
        if repairing and args.dry_run_repair:
            payload["repair_plan"] = _repair_plan(
                payload,
                repair_fts_flag=args.repair_fts,
                repair_vectors_flag=args.repair_vectors or args.rebuild_vectors_force,
                repair_embedding_refs_flag=args.repair_embedding_refs,
            )
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
