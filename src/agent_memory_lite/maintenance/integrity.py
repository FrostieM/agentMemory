"""Read-only memory retrieval integrity audit."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.fts.chunks_fts import rebuild_chunks_fts
from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.repositories.maintenance_repo import count_open_maintenance_events
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


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


def _sample_query(conn: sqlite3.Connection, workspace_id: str) -> tuple[str, str] | None:
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
    tokens = [token for token in _TOKEN_RE.findall(str(row["text"])) if len(token) >= 4]
    if not tokens:
        return None
    return str(row["id"]), tokens[0]


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


def _vector_check(
    conn: sqlite3.Connection,
    workspace_id: str,
    vector_store: VectorStore | None,
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
    status = "ok" if not missing and not extra else "degraded"
    return IntegrityCheck(
        status=status,
        details={
            "chunks": len(chunk_ids),
            "vectors": len(vector_ids),
            "missing": len(missing),
            "extra": len(extra),
            "missing_ids_sample": missing[:10],
            "extra_ids_sample": extra[:10],
        },
    )


def _roundtrip_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    sample = _sample_query(conn, workspace_id)
    if sample is None:
        return IntegrityCheck(status="unknown", details={"reason": "no searchable chunk"})
    expected_chunk_id, token = sample
    hits = search_chunks_fts(conn, workspace_id=workspace_id, query=token, limit=10)
    fts_hit_ids = {hit.chunk_id for hit in hits}
    fts_ok = bool(hits)
    built = build_context(
        conn,
        RetrievalQuery(workspace_id=workspace_id, query=token, max_tokens=1200),
        embedding_provider=None,
        vector_store=None,
    )
    context_hit_ids = {hit.id for hit in built.hits}
    context_ok = bool(context_hit_ids & fts_hit_ids)
    status = "ok" if fts_ok and context_ok else "degraded"
    return IntegrityCheck(
        status=status,
        details={
            "query": token,
            "expected_chunk_id": expected_chunk_id,
            "fts_ok": fts_ok,
            "context_ok": context_ok,
            "context_hits": len(built.hits),
            "shared_context_fts_hits": len(context_hit_ids & fts_hit_ids),
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


def run_integrity_audit(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    vector_store: VectorStore | None = None,
) -> IntegrityReport:
    checks = {
        "sqlite": _sqlite_check(conn),
        "workspace_pollution": _workspace_pollution_check(conn, workspace_id),
        "fts": _fts_check(conn, workspace_id),
        "vector": _vector_check(conn, workspace_id, vector_store),
        "retrieval_roundtrip": _roundtrip_check(conn, workspace_id),
        "maintenance_events": _maintenance_check(conn, workspace_id),
        "capability_links": _capability_links_check(conn, workspace_id),
    }
    failures = [name for name, check in checks.items() if check.status not in {"ok", "unknown"}]
    unknown = [name for name, check in checks.items() if check.status == "unknown"]
    status = "degraded" if failures else ("unknown" if unknown else "ok")
    repair_hints: list[str] = []
    if checks["fts"].status == "degraded":
        repair_hints.append("Run scripts/memory_audit.py --repair-fts --backup-first.")
    if checks["vector"].status == "degraded":
        repair_hints.append("Run scripts/memory_audit.py --repair-vectors --backup-first.")
    if checks["workspace_pollution"].status == "degraded":
        repair_hints.append("Inspect workspace_id rows before migrating or deleting them.")
    if checks["maintenance_events"].status == "degraded":
        repair_hints.append("Inspect open maintenance_events before trusting retrieval.")
    if checks["capability_links"].status == "degraded":
        repair_hints.append(
            "Inspect dangling capability_links before trusting role/skill guidance."
        )

    return IntegrityReport(
        status=status,
        workspace_id=workspace_id,
        checks=checks,
        counts={
            "chunks": checks["fts"].details.get("chunks", 0),
            "chunks_fts": checks["fts"].details.get("chunks_fts", 0),
            "vectors": checks["vector"].details.get("vectors"),
            "open_maintenance_events": checks["maintenance_events"].details.get("open_events"),
            "capability_links": checks["capability_links"].details.get("links"),
        },
        failures=failures,
        repair_hints=repair_hints,
    )


def repair_fts(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    return rebuild_chunks_fts(conn, workspace_id=workspace_id)
