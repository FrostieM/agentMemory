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
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
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
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/get_context",
        operation="get_context",
        label="Build agent context",
        snippet=body.query,
    ) as trace:
        trace.stage_done(
            "input",
            "Context query accepted",
            counts={
                "files_in_scope": len(body.files_in_scope),
                "max_tokens": body.max_tokens,
                "historical": body.historical,
            },
            snippet=body.query,
        )
        query = RetrievalQuery(
            workspace_id=body.workspace_id,
            session_id=body.session_id,
            task_id=body.task_id,
            query=body.query,
            files_in_scope=body.files_in_scope,
            max_tokens=body.max_tokens,
            historical=body.historical,
        )
        trace.stage_started("retrieve", "Retrieve matching memory")
        built = build_context(
            conn,
            query,
            embedding_provider=provider,
            vector_store=store,
        )
        trace.stage_done(
            "retrieve", "Memory candidates ranked", counts={"sources": len(built.hits)}
        )
        trace.stage_started("context", "Build XML envelope")
        response = GetContextResponse(
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
        trace.stage_done(
            "context",
            "Context envelope built",
            counts={
                "sources": len(response.sources),
                "chars": len(response.context_text),
            },
        )
        trace.stage_done(
            "response", "Context response ready", counts={"sources": len(response.sources)}
        )
        return response


@router.post("/memory/explain_context", response_model=ExplainContextResponse)
def explain_context_route(
    body: GetContextRequest,
    conn: DbDep,
    provider: EmbeddingProviderDep,
    store: VectorStoreDep,
    settings: SettingsDep,
) -> ExplainContextResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/explain_context",
        operation="explain_context",
        label="Explain context selection",
        snippet=body.query,
    ) as trace:
        trace.stage_done(
            "input",
            "Explain query accepted",
            counts={"max_tokens": body.max_tokens, "files_in_scope": len(body.files_in_scope)},
            snippet=body.query,
        )
        query = RetrievalQuery(
            workspace_id=body.workspace_id,
            session_id=body.session_id,
            task_id=body.task_id,
            query=body.query,
            files_in_scope=body.files_in_scope,
            max_tokens=body.max_tokens,
            historical=body.historical,
        )
        trace.stage_started("fts", "Collect exact candidates")
        trace.stage_started("vector", "Collect semantic candidates")
        report = explain_context(
            conn,
            query,
            embedding_provider=provider,
            vector_store=store,
        )
        payload = report.to_dict()
        source_candidates = list(payload.get("source_candidates", []))
        scored_candidates = list(payload.get("scored_candidates", []))
        included_ids = list(payload.get("included_ids", []))
        fts_count = sum(1 for item in source_candidates if item.get("source") == "fts")
        vector_count = sum(1 for item in source_candidates if item.get("source") == "vector")
        trace.stage_done("fts", "Exact candidates collected", counts={"candidates": fts_count})
        trace.stage_done(
            "vector", "Semantic candidates collected", counts={"candidates": vector_count}
        )
        trace.stage_started("rank", "Rank and fuse candidates")
        trace.stage_done(
            "rank",
            "Candidates ranked",
            counts={"candidates": len(scored_candidates), "included": len(included_ids)},
        )
        trace.stage_started("budget", "Apply context budget")
        trace.stage_done(
            "budget",
            "Context budget applied",
            counts={
                "context_tokens": payload.get("context_tokens"),
                "included": len(included_ids),
            },
        )
        response = ExplainContextResponse.model_validate(payload)
        trace.stage_done(
            "response",
            "Explain response ready",
            counts={"included": len(response.included_ids)},
        )
        return response
