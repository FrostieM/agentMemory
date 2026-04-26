"""GET /health — liveness + configuration sanity check.

Reports the configured embedding/LLM provider and the schema version. Phase 0
ships a static dim until the embedding provider is wired (Phase 1).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from agent_memory_lite.api.deps import DbDep, SettingsDep
from agent_memory_lite.version import __version__

router = APIRouter()


class HealthResponse(BaseModel):
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


@router.get("/health", response_model=HealthResponse)
def health(settings: SettingsDep, conn: DbDep) -> HealthResponse:
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    versions = [str(row[0]) for row in rows]
    return HealthResponse(
        status="ok",
        version=__version__,
        db="ok",
        workspace_id=settings.workspace_id,
        embedding_backend=settings.embedding_backend,
        embedding_model=settings.embedding_model,
        vector_backend=settings.vector_backend,
        llm_backend=settings.llm_backend,
        llm_model=settings.llm_model,
        applied_migrations=versions,
    )
