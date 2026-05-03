"""POST /memory/get_context — primary retrieval surface.

Telemetry helpers live in ``context_trace.py``; the
``/memory/explain_context`` route lives in ``context_explain.py`` and
is mounted on the same router below. Opportunistic post-build hooks
(behavior tracking, last_retrieved, sentinel scheduler, pending_review
injection) live in ``context_post_build.py``.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    EmbeddingProviderDep,
    SettingsDep,
    VectorStoreDep,
    ensure_workspace_readable,
)
from agent_memory_lite.api.routes.context_explain import router as explain_router
from agent_memory_lite.api.routes.context_post_build import apply_post_build_hooks
from agent_memory_lite.api.routes.context_trace import trace_used_context_objects
from agent_memory_lite.api.schemas.context import (
    ContextSource,
    GetContextRequest,
    GetContextResponse,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.retrieval.explain import used_context_objects

router = APIRouter()
router.include_router(explain_router)


@router.post("/memory/get_context", response_model=GetContextResponse)
def get_context_route(
    body: GetContextRequest,
    conn: DbDep,
    provider: EmbeddingProviderDep,
    store: VectorStoreDep,
    settings: SettingsDep,
) -> GetContextResponse:
    ensure_workspace_readable(body.workspace_id, settings)
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
        built = build_context(conn, query, embedding_provider=provider, vector_store=store)
        trace.stage_done(
            "retrieve", "Memory candidates ranked", counts={"sources": len(built.hits)}
        )
        # Single chokepoint for behavior_apply / last_retrieved_at / sentinel
        # scheduler / pending_review injection. The MCP stdio local fallback
        # uses the same helper so flag behaviour matches across both surfaces.
        envelope_text = apply_post_build_hooks(
            conn,
            settings=settings,
            request_workspace_id=body.workspace_id,
            built=built,
            envelope_text=built.text,
            embedding_provider=provider,
            vector_store=store,
        )

        trace.stage_started("context", "Build XML envelope")
        response = GetContextResponse(
            context_text=envelope_text,
            sources=[
                ContextSource(
                    type="chunk",
                    id=hit.id,
                    score=hit.score,
                    sources=hit.sources,
                    path=hit.path,
                    text=(hit.text or "")[:160],
                    metadata=hit.metadata,
                )
                for hit in built.hits
            ],
            budget_diagnostics=built.budget_diagnostics,
        )
        trace.stage_done(
            "context",
            "Context envelope built",
            counts={"sources": len(response.sources), "chars": len(response.context_text)},
        )
        used_objects = used_context_objects(built)
        trace_used_context_objects(trace, used_objects)
        trace.stage_done(
            "context",
            "Context objects traced",
            counts={"used_context_objects": len(used_objects)},
        )
        trace.stage_done(
            "response", "Context response ready", counts={"sources": len(response.sources)}
        )
        return response
