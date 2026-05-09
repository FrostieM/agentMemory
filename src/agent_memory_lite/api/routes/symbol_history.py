"""POST /memory/symbol_history — version chain for one symbol.

1.6.0: returns every recorded version of a given qualified_name in
descending chronological order so the agent can answer "what changed
in paperBot.calculate over the last 7 days?" without scanning the
audit log.

Each version row carries the signature_text + content_hash that were
captured at ingest time, so even after the underlying chunk has been
deleted (file re-ingested with newer content) the historical
signature stays visible.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.repositories.symbol_versions_repo import list_versions_for_qname

router = APIRouter()


class SymbolHistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    qualified_name: str = Field(max_length=400)
    limit: int = Field(default=20, ge=1, le=200)


class VersionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str
    qualified_name: str
    file_path: str | None
    chunk_id: str | None
    language: str | None
    signature_text: str
    signature_hash: str
    content_hash: str
    created_at: str


class SymbolHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    qualified_name: str
    total: int
    versions: list[VersionRow]


@router.post("/memory/symbol_history", response_model=SymbolHistoryResponse)
def symbol_history_route(
    payload: SymbolHistoryRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> SymbolHistoryResponse:
    ensure_workspace_readable(payload.workspace_id, settings)
    rows = list_versions_for_qname(
        conn,
        workspace_id=payload.workspace_id,
        qualified_name=payload.qualified_name,
        limit=payload.limit,
    )
    versions = [
        VersionRow(
            version_id=v.id,
            qualified_name=v.qualified_name,
            file_path=v.file_path,
            chunk_id=v.chunk_id,
            language=v.language,
            signature_text=v.signature_text,
            signature_hash=v.signature_hash,
            content_hash=v.content_hash,
            created_at=v.created_at,
        )
        for v in rows
    ]
    return SymbolHistoryResponse(
        workspace_id=payload.workspace_id,
        qualified_name=payload.qualified_name,
        total=len(versions),
        versions=versions,
    )
