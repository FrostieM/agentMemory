"""POST /memory/explain_context — retrieval explainability route.

Split out of ``context.py`` so the routes file stays under the SLOC
ceiling. ``context.py`` mounts this router on the same prefix.
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
from agent_memory_lite.api.routes.context_trace import trace_used_context_objects
from agent_memory_lite.api.schemas.context import ExplainContextResponse, GetContextRequest
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.explain import explain_context

router = APIRouter()


@router.post("/memory/explain_context", response_model=ExplainContextResponse)
def explain_context_route(
    body: GetContextRequest,
    conn: DbDep,
    provider: EmbeddingProviderDep,
    store: VectorStoreDep,
    settings: SettingsDep,
) -> ExplainContextResponse:
    ensure_workspace_readable(body.workspace_id, settings)
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
        report = explain_context(conn, query, embedding_provider=provider, vector_store=store)
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
        trace_used_context_objects(trace, list(response.used_context_objects))
        trace.stage_done(
            "context",
            "Context objects traced",
            counts={"used_context_objects": len(response.used_context_objects)},
        )
        trace.stage_done(
            "response",
            "Explain response ready",
            counts={"included": len(response.included_ids)},
        )
        return response
