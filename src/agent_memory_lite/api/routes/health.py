"""GET /health - liveness plus retrieval-integrity summary."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import DbDep, SettingsDep, VectorStoreDep
from agent_memory_lite.maintenance.integrity import run_integrity_audit
from agent_memory_lite.repositories.vector_metadata_repo import provider_name_from_settings
from agent_memory_lite.version import __version__

router = APIRouter()


class RetrievalIntegritySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    counts: dict[str, Any]
    failures: list[str]
    warnings: list[str]
    repair_hints: list[str]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    version: str
    db: str
    workspace_id: str
    embedding_backend: str
    embedding_model: str
    vector_backend: str
    llm_backend: str
    llm_model: str
    applied_migrations: list[str]
    retrieval_integrity: RetrievalIntegritySummary


@router.get("/health", response_model=HealthResponse)
def health(settings: SettingsDep, conn: DbDep, store: VectorStoreDep) -> HealthResponse:
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    versions = [str(row[0]) for row in rows]
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
    return HealthResponse(
        status="degraded" if report.status == "degraded" else "ok",
        version=__version__,
        db="ok",
        workspace_id=settings.workspace_id,
        embedding_backend=settings.embedding_backend,
        embedding_model=settings.embedding_model,
        vector_backend=settings.vector_backend,
        llm_backend=settings.llm_backend,
        llm_model=settings.llm_model,
        applied_migrations=versions,
        retrieval_integrity=RetrievalIntegritySummary(
            status=report.status,
            counts=report.counts,
            failures=report.failures,
            warnings=report.warnings,
            repair_hints=report.repair_hints,
        ),
    )
