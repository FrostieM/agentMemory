"""Per-case runner for the retrieval-quality eval.

Split out of ``retrieval_quality.py``. Grading helpers live in
``retrieval_quality_grading.py``; this module owns the live v3
``memory_search`` + ``memory_brief`` checks and assembles the result row.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.evals.metrics import hit_rate, ndcg_at_k, recall_at_k, reciprocal_rank
from agent_memory_lite.maintenance.retrieval_quality_grading import (
    grade_failures,
    render_levels_from_diagnostics,
)
from agent_memory_lite.maintenance.retrieval_quality_models import (
    RetrievalQualityCase,
    RetrievalQualityResult,
)
from agent_memory_lite.storage.reader import SearchHit, get_object, search
from agent_memory_lite.vector_store.base import VectorStore


def _hit_id(hit: SearchHit) -> str:
    return str(hit.projection.get("id") or "")


def _hit_sources(hit: SearchHit) -> list[str]:
    if hit.kind == "chunk":
        return ["fts"]
    return [hit.kind]


def _render_search_hits(
    conn: sqlite3.Connection,
    workspace_id: str,
    hits: list[SearchHit],
) -> str:
    lines: list[str] = []
    for hit in hits:
        projection = {k: v for k, v in hit.projection.items() if v not in (None, "", [], {})}
        if hit.kind == "chunk":
            chunk = get_object(
                conn,
                workspace_id=workspace_id,
                kind="chunk",
                object_id=_hit_id(hit),
                fields=["text"],
            )
            if chunk and chunk.get("text"):
                projection["text"] = chunk["text"]
        lines.append(f"{hit.kind}: {projection}")
    return "\n".join(lines)


def _brief_diagnostics(sections: list[Any], *, max_tokens: int, token_count: int) -> dict[str, Any]:
    return {
        "max_tokens": max_tokens,
        "token_count": token_count,
        "sections": [
            {
                "name": section.name,
                "render_level": "summary" if section.lines else "none",
                "objects_included": max(0, len(section.lines) - 1),
            }
            for section in sections
        ],
        "omissions": [],
    }


def run_case(
    conn: sqlite3.Connection,
    workspace_id: str,
    case: RetrievalQualityCase,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> RetrievalQualityResult:
    del embedding_provider, vector_store
    hits = search(
        conn,
        workspace_id=workspace_id,
        query=case.query,
        limit=max(case.top_k, 10),
    )
    brief = compose_brief(
        conn,
        workspace_id=workspace_id,
        task=case.query,
        max_tokens=case.max_tokens,
    )
    top_hits = hits[: case.top_k]
    retrieved_ids = [_hit_id(hit) for hit in top_hits if _hit_id(hit)]
    source_map = {_hit_id(hit): _hit_sources(hit) for hit in top_hits if _hit_id(hit)}
    matched_ids = [item for item in case.expected_ids if item in retrieved_ids]
    expected_context_ids = list(dict.fromkeys([*case.expected_ids, *case.expected_context_ids]))
    rendered = brief.body_md + "\n" + _render_search_hits(conn, workspace_id, hits)
    matched_context_ids = [
        item for item in expected_context_ids if item in retrieved_ids or item in rendered
    ]
    matched_object_titles = [item for item in case.expected_object_titles if item in rendered]
    budget_diagnostics = _brief_diagnostics(
        brief.sections,
        max_tokens=case.max_tokens,
        token_count=brief.token_count,
    )
    render_levels = render_levels_from_diagnostics(budget_diagnostics)

    failures = grade_failures(
        case=case,
        retrieved_ids=retrieved_ids,
        matched_ids=matched_ids,
        matched_context_ids=matched_context_ids,
        matched_object_titles=matched_object_titles,
        source_map=source_map,
        render_levels=render_levels,
        built_text=rendered,
        budget_diagnostics=budget_diagnostics,
    )

    return RetrievalQualityResult(
        name=case.name,
        status="failed" if failures else "passed",
        query=case.query,
        top_k=case.top_k,
        expected_ids=case.expected_ids,
        matched_ids=matched_ids,
        retrieved_ids=retrieved_ids,
        expected_context_ids=expected_context_ids,
        matched_context_ids=matched_context_ids,
        expected_object_titles=case.expected_object_titles,
        matched_object_titles=matched_object_titles,
        expected_sources=case.expected_sources,
        source_map=source_map,
        render_levels=render_levels,
        budget_diagnostics=budget_diagnostics,
        metrics={
            "recall_at_k": recall_at_k(retrieved_ids, case.expected_ids, k=case.top_k),
            "reciprocal_rank": reciprocal_rank(retrieved_ids, case.expected_ids, k=case.top_k),
            "ndcg_at_k": ndcg_at_k(retrieved_ids, case.expected_ids, k=case.top_k),
            "context_hit_rate": hit_rate(matched_context_ids, expected_context_ids),
        },
        failures=failures,
    )
