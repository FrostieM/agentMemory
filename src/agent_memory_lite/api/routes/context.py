"""POST /memory/get_context — primary retrieval surface."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    EmbeddingProviderDep,
    SettingsDep,
    VectorStoreDep,
    ensure_workspace_allowed,
)
from agent_memory_lite.api.schemas.context import (
    ContextSource,
    ExplainContextResponse,
    GetContextRequest,
    GetContextResponse,
)
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.retrieval.explain import explain_context

router = APIRouter()


@router.post("/memory/get_context", response_model=GetContextResponse)
def get_context_route(
    body: GetContextRequest,
    conn: DbDep,
    provider: EmbeddingProviderDep,
    store: VectorStoreDep,
    settings: SettingsDep,
) -> GetContextResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    query = RetrievalQuery(
        workspace_id=body.workspace_id,
        session_id=body.session_id,
        task_id=body.task_id,
        query=body.query,
        files_in_scope=body.files_in_scope,
        max_tokens=body.max_tokens,
        historical=body.historical,
    )
    built = build_context(
        conn,
        query,
        embedding_provider=provider,
        vector_store=store,
    )
    return GetContextResponse(
        context_text=built.text,
        sources=[
            ContextSource(
                type="chunk",
                id=hit.id,
                score=hit.score,
                sources=hit.sources,
                path=hit.path,
                metadata=hit.metadata,
            )
            for hit in built.hits
        ],
    )


@router.post("/memory/explain_context", response_model=ExplainContextResponse)
def explain_context_route(
    body: GetContextRequest,
    conn: DbDep,
    provider: EmbeddingProviderDep,
    store: VectorStoreDep,
    settings: SettingsDep,
) -> ExplainContextResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    query = RetrievalQuery(
        workspace_id=body.workspace_id,
        session_id=body.session_id,
        task_id=body.task_id,
        query=body.query,
        files_in_scope=body.files_in_scope,
        max_tokens=body.max_tokens,
        historical=body.historical,
    )
    report = explain_context(
        conn,
        query,
        embedding_provider=provider,
        vector_store=store,
    )
    return ExplainContextResponse.model_validate(report.to_dict())
