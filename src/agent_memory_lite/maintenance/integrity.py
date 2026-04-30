"""Read-only memory retrieval integrity audit."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_memory_lite.fts.chunks_fts import rebuild_chunks_fts
from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.repositories.maintenance_repo import count_open_maintenance_events
from agent_memory_lite.repositories.vector_metadata_repo import (
    CHUNKING_STRATEGY,
    VECTOR_SCHEMA_VERSION,
    get_vector_index_metadata,
)
from agent_memory_lite.repositories.workspace_manifest_repo import get_workspace_manifest
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)
_ROUNDTRIP_STOPWORDS = {
    "about",
    "after",
    "against",
    "before",
    "candidate",
    "completed",
    "context",
    "decision",
    "default",
    "episode",
    "memory",
    "project",
    "reports",
    "status",
    "system",
    "theory",
    "workspace",
}


@dataclass(frozen=True, slots=True)
class IntegrityCheck:
    status: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    status: str
    workspace_id: str
    checks: dict[str, IntegrityCheck]
    counts: dict[str, Any]
    failures: list[str]
    warnings: list[str]
    repair_hints: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "checks": {
                name: {"status": check.status, "details": check.details}
                for name, check in self.checks.items()
            },
            "counts": self.counts,
            "failures": self.failures,
            "warnings": self.warnings,
            "repair_hints": self.repair_hints,
        }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'virtual table')",
        (table,),
    ).fetchone()
    return row is not None


def _workspace_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'virtual table') AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    tables: list[str] = []
    for row in rows:
        table = str(row[0])
        try:
            cols = [str(col[1]) for col in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        except sqlite3.OperationalError:
            continue
        if "workspace_id" in cols:
            tables.append(table)
    return tables


def _count(conn: sqlite3.Connection, query: str, args: tuple[object, ...]) -> int:
    row = conn.execute(query, args).fetchone()
    return int(row[0]) if row else 0


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sample_query(conn: sqlite3.Connection, workspace_id: str) -> tuple[str, list[str]] | None:
    row = conn.execute(
        """
        SELECT id, text
        FROM chunks
        WHERE workspace_id = ? AND length(text) > 0
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (workspace_id,),
    ).fetchone()
    if row is None:
        return None
    raw_tokens = [token for token in _TOKEN_RE.findall(str(row["text"])) if len(token) >= 4]
    tokens = [
        token
        for token in raw_tokens
        if token.lower() not in _ROUNDTRIP_STOPWORDS and not token.isdigit()
    ]
    tokens.sort(key=lambda token: (len(token), token), reverse=True)
    if len(tokens) < 3:
        tokens.extend(raw_tokens)
    tokens = list(dict.fromkeys(tokens))[:8]
    if not tokens:
        return None
    return str(row["id"]), tokens


def _sqlite_check(conn: sqlite3.Connection) -> IntegrityCheck:
    integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    status = "ok" if integrity == "ok" and quick == "ok" and not fk_rows else "degraded"
    return IntegrityCheck(
        status=status,
        details={
            "integrity_check": integrity,
            "quick_check": quick,
            "foreign_key_violations": len(fk_rows),
        },
    )


def _workspace_manifest_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    if not _table_exists(conn, "workspace_manifest"):
        return IntegrityCheck(status="degraded", details={"error": "workspace_manifest missing"})
    manifest = get_workspace_manifest(conn)
    if manifest is None:
        return IntegrityCheck(status="warning", details={"error": "workspace_manifest empty"})
    status = "ok" if manifest.workspace_id == workspace_id else "degraded"
    return IntegrityCheck(
        status=status,
        details={
            "workspace_id": manifest.workspace_id,
            "expected_workspace_id": workspace_id,
            "db_uuid": manifest.db_uuid,
            "last_audit_at": manifest.last_audit_at,
            "last_audit_status": manifest.last_audit_status,
            "last_repair_at": manifest.last_repair_at,
        },
    )


def _workspace_pollution_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    default_rows: dict[str, int] = {}
    other_rows: dict[str, int] = {}
    for table in _workspace_tables(conn):
        default_count = _count(
            conn,
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id = 'default'",
            (),
        )
        if default_count:
            default_rows[table] = default_count
        other_count = _count(
            conn,
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id NOT IN (?, 'default')",
            (workspace_id,),
        )
        if other_count:
            other_rows[table] = other_count
    status = "ok"
    if (workspace_id != "default" and default_rows) or other_rows:
        status = "degraded"
    return IntegrityCheck(
        status=status,
        details={"default_rows": default_rows, "other_workspace_rows": other_rows},
    )


def _fts_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    if not _table_exists(conn, "chunks_fts"):
        return IntegrityCheck(status="degraded", details={"error": "chunks_fts missing"})
    chunk_count = _count(
        conn, "SELECT COUNT(*) FROM chunks WHERE workspace_id = ?", (workspace_id,)
    )
    fts_count = _count(
        conn,
        "SELECT COUNT(*) FROM chunks_fts WHERE workspace_id = ?",
        (workspace_id,),
    )
    missing = _count(
        conn,
        """
        SELECT COUNT(*)
        FROM chunks c
        LEFT JOIN chunks_fts f ON f.chunk_id = c.id
        WHERE c.workspace_id = ? AND f.chunk_id IS NULL
        """,
        (workspace_id,),
    )
    extra = _count(
        conn,
        """
        SELECT COUNT(*)
        FROM chunks_fts f
        LEFT JOIN chunks c ON c.id = f.chunk_id
        WHERE f.workspace_id = ? AND c.id IS NULL
        """,
        (workspace_id,),
    )
    workspace_mismatch = _count(
        conn,
        """
        SELECT COUNT(*)
        FROM chunks c
        JOIN chunks_fts f ON f.chunk_id = c.id
        WHERE c.workspace_id = ? AND f.workspace_id != c.workspace_id
        """,
        (workspace_id,),
    )
    status = (
        "ok"
        if chunk_count == fts_count and missing == 0 and extra == 0 and workspace_mismatch == 0
        else "degraded"
    )
    return IntegrityCheck(
        status=status,
        details={
            "chunks": chunk_count,
            "chunks_fts": fts_count,
            "missing": missing,
            "extra": extra,
            "workspace_mismatch": workspace_mismatch,
        },
    )


def _vector_check(  # noqa: PLR0912
    conn: sqlite3.Connection,
    workspace_id: str,
    vector_store: VectorStore | None,
    *,
    expected_provider_name: str | None = None,
    expected_vector_backend: str | None = None,
) -> IntegrityCheck:
    chunk_ids = {
        str(row[0])
        for row in conn.execute(
            "SELECT id FROM chunks WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
    }
    if vector_store is None:
        return IntegrityCheck(
            status="unknown",
            details={"chunks": len(chunk_ids), "reason": "vector_store_not_supplied"},
        )
    try:
        vector_store.open()
        vector_ids = set(vector_store.list_ids(NAMESPACE_CHUNKS, workspace_id=workspace_id))
    except Exception as exc:
        return IntegrityCheck(
            status="unknown",
            details={"chunks": len(chunk_ids), "error": str(exc)},
        )
    missing = sorted(chunk_ids - vector_ids)
    extra = sorted(vector_ids - chunk_ids)
    if vector_ids:
        placeholders = ",".join("?" for _ in vector_ids)
        missing_embedding_ids = _count(
            conn,
            f"""
            SELECT COUNT(*)
            FROM chunks
            WHERE workspace_id = ?
              AND id IN ({placeholders})
              AND (embedding_id IS NULL OR embedding_id != id)
            """,
            (workspace_id, *sorted(vector_ids)),
        )
    else:
        missing_embedding_ids = 0
    metadata = get_vector_index_metadata(
        conn,
        workspace_id=workspace_id,
        namespace=NAMESPACE_CHUNKS,
    )
    metadata_status = "ok"
    metadata_details: dict[str, Any] = {}
    if metadata is None:
        metadata_status = "warning" if vector_ids else "unknown"
        metadata_details["reason"] = "vector_index_metadata_missing"
    else:
        metadata_details = {
            "provider_name": metadata.provider_name,
            "embedding_dim": metadata.embedding_dim,
            "vector_backend": metadata.vector_backend,
            "chunking_strategy": metadata.chunking_strategy,
            "schema_version": metadata.schema_version,
            "row_count": metadata.row_count,
            "updated_at": metadata.updated_at,
        }
        metadata_mismatches: dict[str, dict[str, Any]] = {}
        if metadata.row_count != len(vector_ids):
            metadata_mismatches["row_count"] = {
                "expected": len(vector_ids),
                "actual": metadata.row_count,
            }
        if expected_provider_name and metadata.provider_name != expected_provider_name:
            metadata_mismatches["provider_name"] = {
                "expected": expected_provider_name,
                "actual": metadata.provider_name,
            }
        if expected_vector_backend and metadata.vector_backend != expected_vector_backend:
            metadata_mismatches["vector_backend"] = {
                "expected": expected_vector_backend,
                "actual": metadata.vector_backend,
            }
        if metadata.chunking_strategy != CHUNKING_STRATEGY:
            metadata_mismatches["chunking_strategy"] = {
                "expected": CHUNKING_STRATEGY,
                "actual": metadata.chunking_strategy,
            }
        if metadata.schema_version != VECTOR_SCHEMA_VERSION:
            metadata_mismatches["schema_version"] = {
                "expected": VECTOR_SCHEMA_VERSION,
                "actual": metadata.schema_version,
            }
        if metadata_mismatches:
            metadata_status = "degraded"
            metadata_details["mismatches"] = metadata_mismatches

    if missing or extra or metadata_status == "degraded":
        status = "degraded"
    elif missing_embedding_ids or metadata_status == "warning":
        status = "warning"
    else:
        status = "ok"
    return IntegrityCheck(
        status=status,
        details={
            "chunks": len(chunk_ids),
            "vectors": len(vector_ids),
            "missing": len(missing),
            "extra": len(extra),
            "missing_embedding_ids": missing_embedding_ids,
            "missing_ids_sample": missing[:10],
            "extra_ids_sample": extra[:10],
            "metadata_status": metadata_status,
            "metadata": metadata_details,
        },
    )


def _roundtrip_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    sample = _sample_query(conn, workspace_id)
    if sample is None:
        return IntegrityCheck(status="unknown", details={"reason": "no searchable chunk"})
    expected_chunk_id, tokens = sample
    tried: list[dict[str, Any]] = []
    for token in tokens:
        hits = search_chunks_fts(conn, workspace_id=workspace_id, query=token, limit=10)
        fts_hit_ids = {hit.chunk_id for hit in hits}
        built = build_context(
            conn,
            RetrievalQuery(workspace_id=workspace_id, query=token, max_tokens=1200),
            embedding_provider=None,
            vector_store=None,
        )
        context_hit_ids = {hit.id for hit in built.hits}
        shared = context_hit_ids & fts_hit_ids
        tried.append(
            {
                "query": token,
                "fts_hits": len(hits),
                "context_hits": len(built.hits),
                "shared_context_fts_hits": len(shared),
            }
        )
        if hits and shared:
            return IntegrityCheck(
                status="ok",
                details={
                    "query": token,
                    "expected_chunk_id": expected_chunk_id,
                    "fts_ok": True,
                    "context_ok": True,
                    "context_hits": len(built.hits),
                    "shared_context_fts_hits": len(shared),
                },
            )
    status = "degraded"
    return IntegrityCheck(
        status=status,
        details={
            "query": tokens[0],
            "expected_chunk_id": expected_chunk_id,
            "fts_ok": any(item["fts_hits"] for item in tried),
            "context_ok": False,
            "context_hits": max((item["context_hits"] for item in tried), default=0),
            "shared_context_fts_hits": 0,
            "tried": tried,
        },
    )


def _maintenance_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    if not _table_exists(conn, "maintenance_events"):
        return IntegrityCheck(status="unknown", details={"reason": "maintenance_events missing"})
    open_events = count_open_maintenance_events(conn, workspace_id=workspace_id)
    return IntegrityCheck(
        status="ok" if open_events == 0 else "degraded",
        details={"open_events": open_events},
    )


def _capability_links_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    if not _table_exists(conn, "capability_links"):
        return IntegrityCheck(status="unknown", details={"reason": "capability_links missing"})

    total = _count(
        conn,
        "SELECT COUNT(*) FROM capability_links WHERE workspace_id = ?",
        (workspace_id,),
    )
    target_tables = {
        "theory": "theories",
        "theory_evidence": "theory_evidence",
        "experiment": "research_experiments",
        "experiment_result": "experiment_results",
        "research_insight": "research_insights",
        "memory_candidate": "memory_candidates",
        "decision": "decisions",
    }
    capability_tables = {
        "role": "agent_roles",
        "skill": "agent_skills",
        "playbook": "agent_playbooks",
    }

    missing_targets: dict[str, int] = {}
    for target_type, table in target_tables.items():
        count = _count(
            conn,
            f"""
            SELECT COUNT(*)
            FROM capability_links l
            LEFT JOIN {table} t
              ON t.id = l.target_id AND t.workspace_id = l.workspace_id
            WHERE l.workspace_id = ?
              AND l.target_type = ?
              AND t.id IS NULL
            """,
            (workspace_id, target_type),
        )
        if count:
            missing_targets[target_type] = count

    missing_capabilities: dict[str, int] = {}
    for capability_type, table in capability_tables.items():
        count = _count(
            conn,
            f"""
            SELECT COUNT(*)
            FROM capability_links l
            LEFT JOIN {table} c
              ON c.id = l.capability_id AND c.workspace_id = l.workspace_id
            WHERE l.workspace_id = ?
              AND l.capability_type = ?
              AND c.id IS NULL
            """,
            (workspace_id, capability_type),
        )
        if count:
            missing_capabilities[capability_type] = count

    status = "ok" if not missing_targets and not missing_capabilities else "degraded"
    return IntegrityCheck(
        status=status,
        details={
            "links": total,
            "missing_targets": missing_targets,
            "missing_capabilities": missing_capabilities,
        },
    )


def _candidate_hygiene_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    if not _table_exists(conn, "memory_candidates"):
        return IntegrityCheck(status="unknown", details={"reason": "memory_candidates missing"})

    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM memory_candidates
        WHERE workspace_id = ?
        GROUP BY status
        """,
        (workspace_id,),
    ).fetchall()
    by_status = {str(row["status"]): int(row["n"]) for row in rows}
    cutoff = datetime.now(UTC) - timedelta(days=14)
    stale_new = 0
    stale_rows = conn.execute(
        """
        SELECT updated_at
        FROM memory_candidates
        WHERE workspace_id = ? AND status = 'new'
        """,
        (workspace_id,),
    ).fetchall()
    for row in stale_rows:
        updated = _parse_iso(str(row["updated_at"]))
        if updated is not None and updated < cutoff:
            stale_new += 1
    status = "ok" if stale_new == 0 else "warning"
    return IntegrityCheck(
        status=status,
        details={
            "by_status": by_status,
            "new_candidates": by_status.get("new", 0),
            "stale_new_older_than_days": 14,
            "stale_new": stale_new,
        },
    )


def _research_hygiene_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    required = ("theories", "research_experiments", "research_insights")
    missing = [table for table in required if not _table_exists(conn, table)]
    if missing:
        return IntegrityCheck(status="unknown", details={"missing_tables": missing})

    undisciplined_theories = _count(
        conn,
        """
        SELECT COUNT(*)
        FROM theories
        WHERE workspace_id = ?
          AND status IN ('proposed', 'testing', 'supported')
          AND COALESCE(experiment_plan, '') = ''
          AND COALESCE(validation_criteria_json, '[]') IN ('[]', '')
        """,
        (workspace_id,),
    )
    rejected_without_evidence = _count(
        conn,
        """
        SELECT COUNT(*)
        FROM theories
        WHERE workspace_id = ?
          AND status = 'rejected'
          AND evidence_count = 0
        """,
        (workspace_id,),
    )

    open_experiment_rows = conn.execute(
        """
        SELECT updated_at
        FROM research_experiments
        WHERE workspace_id = ? AND status IN ('planned', 'running', 'blocked')
        """,
        (workspace_id,),
    ).fetchall()
    cutoff = datetime.now(UTC) - timedelta(days=30)
    stale_open_experiments = 0
    for row in open_experiment_rows:
        updated = _parse_iso(str(row["updated_at"]))
        if updated is not None and updated < cutoff:
            stale_open_experiments += 1

    warning = (
        undisciplined_theories > 0 or rejected_without_evidence > 0 or stale_open_experiments > 0
    )
    return IntegrityCheck(
        status="warning" if warning else "ok",
        details={
            "undisciplined_active_theories": undisciplined_theories,
            "rejected_theories_without_evidence": rejected_without_evidence,
            "stale_open_experiments_older_than_days": 30,
            "stale_open_experiments": stale_open_experiments,
        },
    )


def _hygiene_report_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    report = run_hygiene_report(conn, workspace_id=workspace_id)
    capability_link_warnings = [
        finding.to_dict()
        for finding in report.findings
        if finding.kind == "missing_capability_link"
    ]
    return IntegrityCheck(
        status=report.status,
        details={
            "counts": report.counts,
            "total_findings": report.counts.get("total_findings", 0),
            "capability_link_warnings": capability_link_warnings[:20],
            "findings_sample": [finding.to_dict() for finding in report.findings[:20]],
        },
    )


def _stray_db_check(db_path: Path | None) -> IntegrityCheck:
    if db_path is None:
        return IntegrityCheck(status="unknown", details={"reason": "db_path_not_supplied"})
    resolved = db_path.resolve()
    if resolved.name != "memory.db" or resolved.parent.name != ".agent_memory":
        return IntegrityCheck(
            status="ok",
            details={"reason": "non_standard_db_path", "db_path": str(resolved)},
        )
    project_root = resolved.parent.parent
    candidates: list[str] = []
    try:
        for candidate in project_root.rglob(".agent_memory/memory.db"):
            candidate_resolved = candidate.resolve()
            if candidate_resolved != resolved:
                candidates.append(str(candidate_resolved))
    except OSError as exc:
        return IntegrityCheck(
            status="unknown",
            details={"db_path": str(resolved), "error": str(exc)},
        )
    return IntegrityCheck(
        status="warning" if candidates else "ok",
        details={
            "db_path": str(resolved),
            "project_root": str(project_root),
            "stray_dbs": candidates,
        },
    )


def run_integrity_audit(  # noqa: PLR0912
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    vector_store: VectorStore | None = None,
    db_path: str | Path | None = None,
    expected_provider_name: str | None = None,
    expected_vector_backend: str | None = None,
) -> IntegrityReport:
    checks = {
        "sqlite": _sqlite_check(conn),
        "workspace_manifest": _workspace_manifest_check(conn, workspace_id),
        "workspace_pollution": _workspace_pollution_check(conn, workspace_id),
        "fts": _fts_check(conn, workspace_id),
        "vector": _vector_check(
            conn,
            workspace_id,
            vector_store,
            expected_provider_name=expected_provider_name,
            expected_vector_backend=expected_vector_backend,
        ),
        "retrieval_roundtrip": _roundtrip_check(conn, workspace_id),
        "maintenance_events": _maintenance_check(conn, workspace_id),
        "capability_links": _capability_links_check(conn, workspace_id),
        "candidate_hygiene": _candidate_hygiene_check(conn, workspace_id),
        "research_hygiene": _research_hygiene_check(conn, workspace_id),
        "hygiene": _hygiene_report_check(conn, workspace_id),
        "stray_dbs": _stray_db_check(Path(db_path) if db_path is not None else None),
    }
    failures = [name for name, check in checks.items() if check.status == "degraded"]
    warnings = [name for name, check in checks.items() if check.status == "warning"]
    unknown = [name for name, check in checks.items() if check.status == "unknown"]
    if failures:
        status = "degraded"
    elif warnings:
        status = "warning"
    elif unknown:
        status = "unknown"
    else:
        status = "ok"
    repair_hints: list[str] = []
    if checks["workspace_manifest"].status == "degraded":
        repair_hints.append("Run migrations and verify MEMORY_WORKSPACE_ID matches this database.")
    if checks["fts"].status == "degraded":
        repair_hints.append("Run scripts/memory_audit.py --repair-fts --backup-first.")
    vector_status = checks["vector"].status
    if vector_status == "degraded":
        repair_hints.append("Run scripts/memory_audit.py --repair-vectors --backup-first.")
    elif vector_status == "warning":
        metadata_status = checks["vector"].details.get("metadata_status")
        if metadata_status == "warning":
            repair_hints.append(
                "Run scripts/memory_audit.py --repair-vectors --backup-first to stamp vector metadata."
            )
        else:
            repair_hints.append(
                "Run scripts/memory_audit.py --repair-embedding-refs --backup-first."
            )
    if checks["workspace_pollution"].status == "degraded":
        repair_hints.append(
            "Run scripts/memory_workspace_doctor.py --workspace <workspace_id> --json "
            "to inspect foreign rows; quarantine only with --quarantine --backup-first "
            "after review."
        )
    if checks["maintenance_events"].status == "degraded":
        repair_hints.append("Inspect open maintenance_events before trusting retrieval.")
    if checks["capability_links"].status == "degraded":
        repair_hints.append(
            "Inspect dangling capability_links before trusting role/skill guidance."
        )
    if checks["candidate_hygiene"].status == "warning":
        repair_hints.append(
            "Review or reject stale memory_candidates; do not leave extractor output untriaged."
        )
    if checks["research_hygiene"].status == "warning":
        repair_hints.append(
            "Add validation criteria, evidence, or completion state to stale research objects."
        )
    if checks["hygiene"].status == "warning":
        repair_hints.append(
            "Run scripts/memory_hygiene.py and triage candidates, theories, experiments, insights, decisions, or capability links."
        )
    if checks["stray_dbs"].status == "warning":
        repair_hints.append(
            "Inspect stray .agent_memory/memory.db files before trusting the selected project DB."
        )

    return IntegrityReport(
        status=status,
        workspace_id=workspace_id,
        checks=checks,
        counts={
            "chunks": checks["fts"].details.get("chunks", 0),
            "chunks_fts": checks["fts"].details.get("chunks_fts", 0),
            "vectors": checks["vector"].details.get("vectors"),
            "missing_embedding_ids": checks["vector"].details.get("missing_embedding_ids"),
            "open_maintenance_events": checks["maintenance_events"].details.get("open_events"),
            "capability_links": checks["capability_links"].details.get("links"),
            "new_candidates": checks["candidate_hygiene"].details.get("new_candidates"),
            "stale_candidates": checks["candidate_hygiene"].details.get("stale_new"),
            "undisciplined_theories": checks["research_hygiene"].details.get(
                "undisciplined_active_theories"
            ),
            "stale_open_experiments": checks["research_hygiene"].details.get(
                "stale_open_experiments"
            ),
            "hygiene_findings": checks["hygiene"].details.get("total_findings"),
            "capability_link_warnings": len(
                checks["hygiene"].details.get("capability_link_warnings", [])
            ),
            "stray_db_warnings": len(checks["stray_dbs"].details.get("stray_dbs", [])),
            "manifest_status": checks["workspace_manifest"].status,
            "hygiene_status": checks["hygiene"].status,
            "vector_metadata_status": checks["vector"].details.get("metadata_status"),
        },
        failures=failures,
        warnings=warnings,
        repair_hints=repair_hints,
    )


def repair_fts(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    return rebuild_chunks_fts(conn, workspace_id=workspace_id)
