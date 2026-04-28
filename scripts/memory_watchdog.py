"""Run integrity, retrieval-quality, and hygiene checks against a live memory DB."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.ingestion.maintenance_writer import write_maintenance_event
from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.maintenance.integrity import run_integrity_audit
from agent_memory_lite.maintenance.retrieval_quality import (
    load_retrieval_quality_cases,
    run_retrieval_quality_evals,
)
from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity
from agent_memory_lite.models.maintenance import MaintenanceEventIn
from agent_memory_lite.repositories.workspace_manifest_repo import (
    ensure_workspace_manifest,
    get_workspace_manifest,
    update_workspace_manifest_audit,
)
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.factory import get_vector_store


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live memory watchdog checks.")
    parser.add_argument("--workspace-id", "--workspace", dest="workspace_id", default=None)
    parser.add_argument("--db", "--db-path", dest="db_path", default=None)
    parser.add_argument("--vectors", "--vector-path", dest="vector_path", default=None)
    parser.add_argument("--sentinels", default=None, help="YAML retrieval quality cases.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-vector", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--no-artifact", action="store_true")
    parser.add_argument("--no-maintenance-event", action="store_true")
    parser.add_argument("--artifact-dir", default=None)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)
    updates: dict[str, Any] = {}
    if args.workspace_id:
        updates["workspace_id"] = args.workspace_id
    if args.db_path:
        updates["db_path"] = Path(args.db_path)
    if args.vector_path:
        updates["vector_db_path"] = Path(args.vector_path)
    return settings.model_copy(update=updates)


def _combined_status(*statuses: str) -> str:
    if "degraded" in statuses:
        return "degraded"
    if "warning" in statuses:
        return "warning"
    if all(status == "unknown" for status in statuses):
        return "unknown"
    return "ok"


def _artifact_path(settings: Settings, args: argparse.Namespace, timestamp: str) -> Path:
    base = Path(args.artifact_dir) if args.artifact_dir else settings.db_path.parent / "audit_runs"
    base.mkdir(parents=True, exist_ok=True)
    safe_stamp = timestamp.replace(":", "").replace("-", "").replace("+", "Z")
    return base / f"memory_watchdog_{safe_stamp}.json"


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")


def _write_watchdog_event(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    status: str,
    payload: dict[str, Any],
) -> str:
    severity = MaintenanceSeverity.ERROR if status == "degraded" else MaintenanceSeverity.WARNING
    event = write_maintenance_event(
        conn,
        MaintenanceEventIn(
            workspace_id=workspace_id,
            kind="memory_watchdog",
            severity=severity,
            status=MaintenanceEventStatus.OPEN,
            summary=f"Memory watchdog reported {status}.",
            details={
                "integrity_status": payload["integrity"]["status"],
                "retrieval_eval_status": payload["retrieval_eval"]["status"],
                "hygiene_status": payload["hygiene"]["status"],
                "failures": payload["failures"],
                "warnings": payload["warnings"],
                "artifact_path": payload.get("artifact_path"),
            },
            source_episode_id=None,
            target_type="memory_watchdog",
            target_id=payload["run_id"],
        ),
    )
    return event.id


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    print(f"integrity={payload['integrity']['status']}")
    print(f"retrieval_eval={payload['retrieval_eval']['status']}")
    print(f"hygiene={payload['hygiene']['status']}")
    if payload["failures"]:
        print("failures=" + json.dumps(payload["failures"], ensure_ascii=False))
    if payload["warnings"]:
        print("warnings=" + json.dumps(payload["warnings"], ensure_ascii=False))
    if payload.get("artifact_path"):
        print(f"artifact_path={payload['artifact_path']}")
    if payload.get("maintenance_event_id"):
        print(f"maintenance_event_id={payload['maintenance_event_id']}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _settings(args)
    conn = open_connection(settings.db_path)
    store = None if args.no_vector else get_vector_store(settings)
    provider = None if args.no_vector else get_embedding_provider(settings)
    timestamp = iso_now()
    try:
        if args.migrate:
            apply_migrations(conn)
            if settings.enforce_workspace_manifest:
                ensure_workspace_manifest(
                    conn,
                    workspace_id=settings.workspace_id,
                    allow_default_workspace=not settings.forbid_default_workspace,
                )
        sentinel_cases = (
            load_retrieval_quality_cases(Path(args.sentinels)) if args.sentinels else []
        )
        integrity = run_integrity_audit(
            conn,
            workspace_id=settings.workspace_id,
            vector_store=store,
            db_path=settings.db_path,
        )
        retrieval_eval = run_retrieval_quality_evals(
            conn,
            workspace_id=settings.workspace_id,
            cases=sentinel_cases,
            embedding_provider=provider,
            vector_store=store,
        )
        hygiene = run_hygiene_report(conn, workspace_id=settings.workspace_id)
        status = _combined_status(integrity.status, retrieval_eval.status, hygiene.status)
        update_workspace_manifest_audit(conn, status=status, audited_at=timestamp)
        manifest = get_workspace_manifest(conn)
        payload: dict[str, Any] = {
            "run_id": f"watchdog_{timestamp}",
            "generated_at": timestamp,
            "status": status,
            "workspace_id": settings.workspace_id,
            "db_path": str(settings.db_path),
            "vector_path": str(settings.vector_db_path),
            "manifest": manifest.model_dump() if manifest is not None else None,
            "integrity": integrity.to_dict(),
            "retrieval_eval": retrieval_eval.to_dict(),
            "hygiene": hygiene.to_dict(),
            "failures": list(integrity.failures) + list(retrieval_eval.failures),
            "warnings": list(integrity.warnings) + ([] if hygiene.status == "ok" else ["hygiene"]),
        }
        if not args.no_artifact:
            artifact = _artifact_path(settings, args, timestamp)
            payload["artifact_path"] = str(artifact)
        if status in {"degraded", "warning"} and not args.no_maintenance_event:
            payload["maintenance_event_id"] = _write_watchdog_event(
                conn,
                workspace_id=settings.workspace_id,
                status=status,
                payload=payload,
            )
        if not args.no_artifact:
            _write_artifact(artifact, payload)
        _print(payload, as_json=args.json)
    except Exception as exc:
        print(f"memory_watchdog_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
        close_connection(conn)
    return 0 if status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
