"""Per-case runner for the retrieval-quality eval.

Split out of ``retrieval_quality.py``. Grading helpers live in
``retrieval_quality_grading.py``; this module owns the live
``build_context`` call and assembles the result row.
"""

from __future__ import annotations

import sqlite3

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
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.vector_store.base import VectorStore


def run_case(
    conn: sqlite3.Connection,
    workspace_id: str,
    case: RetrievalQualityCase,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> RetrievalQualityResult:
    built = build_context(
        conn,
        RetrievalQuery(
            workspace_id=workspace_id,
            query=case.query,
            max_tokens=case.max_tokens,
        ),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    top_hits = built.hits[: case.top_k]
    retrieved_ids = [hit.id for hit in top_hits]
    source_map = {hit.id: list(hit.sources) for hit in top_hits}
    matched_ids = [item for item in case.expected_ids if item in retrieved_ids]
    expected_context_ids = list(dict.fromkeys([*case.expected_ids, *case.expected_context_ids]))
    matched_context_ids = [
        item for item in expected_context_ids if f'id="{item}"' in built.text or item in built.text
    ]
    matched_object_titles = [item for item in case.expected_object_titles if item in built.text]
    render_levels = render_levels_from_diagnostics(built.budget_diagnostics)

    failures = grade_failures(
        case=case,
        retrieved_ids=retrieved_ids,
        matched_ids=matched_ids,
        matched_context_ids=matched_context_ids,
        matched_object_titles=matched_object_titles,
        source_map=source_map,
        render_levels=render_levels,
        built_text=built.text,
        budget_diagnostics=built.budget_diagnostics,
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
        budget_diagnostics=built.budget_diagnostics,
        metrics={
            "recall_at_k": recall_at_k(retrieved_ids, case.expected_ids, k=case.top_k),
            "reciprocal_rank": reciprocal_rank(retrieved_ids, case.expected_ids, k=case.top_k),
            "ndcg_at_k": ndcg_at_k(retrieved_ids, case.expected_ids, k=case.top_k),
            "context_hit_rate": hit_rate(matched_context_ids, expected_context_ids),
        },
        failures=failures,
    )
