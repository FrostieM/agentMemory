"""Live retrieval quality evals for trusted project memory.

The normal eval harness runs against synthetic temporary databases. This module
checks the real selected memory DB: known queries must surface known objects in
`memory_get_context`, with the expected retrieval sources when specified.

Result types and the case loader live in
``retrieval_quality_models.py``; the per-case runner lives in
``retrieval_quality_runner.py``.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.maintenance.retrieval_quality_models import (
    RetrievalQualityCase,
    RetrievalQualityReport,
    RetrievalQualityResult,
    load_retrieval_quality_cases,
)
from agent_memory_lite.maintenance.retrieval_quality_runner import run_case
from agent_memory_lite.vector_store.base import VectorStore

__all__ = [
    "RetrievalQualityCase",
    "RetrievalQualityReport",
    "RetrievalQualityResult",
    "load_retrieval_quality_cases",
    "run_retrieval_quality_evals",
]


def run_retrieval_quality_evals(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    cases: list[RetrievalQualityCase],
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> RetrievalQualityReport:
    results: list[RetrievalQualityResult] = []
    failures: list[str] = []
    for case in cases:
        try:
            result = run_case(
                conn,
                workspace_id,
                case,
                embedding_provider=embedding_provider,
                vector_store=vector_store,
            )
        except Exception as exc:
            result = RetrievalQualityResult(
                name=case.name,
                status="failed",
                query=case.query,
                top_k=case.top_k,
                expected_ids=case.expected_ids,
                matched_ids=[],
                retrieved_ids=[],
                expected_context_ids=case.expected_context_ids,
                matched_context_ids=[],
                expected_object_titles=case.expected_object_titles,
                matched_object_titles=[],
                expected_sources=case.expected_sources,
                source_map={},
                render_levels={},
                budget_diagnostics={},
                metrics={
                    "recall_at_k": 0.0,
                    "reciprocal_rank": 0.0,
                    "ndcg_at_k": 0.0,
                    "context_hit_rate": 0.0,
                },
                failures=[f"{type(exc).__name__}: {exc}"],
            )
        results.append(result)
        failures.extend(f"{result.name}: {failure}" for failure in result.failures)

    cases_passed = sum(1 for result in results if result.status == "passed")
    if not cases:
        status = "unknown"
    elif failures:
        status = "degraded"
    else:
        status = "ok"
    denominator = len(results) or 1
    return RetrievalQualityReport(
        status=status,
        workspace_id=workspace_id,
        cases_run=len(cases),
        cases_passed=cases_passed,
        recall_at_k=sum(result.metrics["recall_at_k"] for result in results) / denominator,
        mrr=sum(result.metrics["reciprocal_rank"] for result in results) / denominator,
        ndcg_at_k=sum(result.metrics["ndcg_at_k"] for result in results) / denominator,
        context_hit_rate=(
            sum(result.metrics["context_hit_rate"] for result in results) / denominator
        ),
        failures=failures,
        results=results,
    )
