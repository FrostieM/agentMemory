"""Memory-trust benchmark orchestrator.

Wires the timing primitive (``benchmark_runner.measure``) to the
specific operations we want to track: integrity audit, hygiene report,
quality gate, FTS search, ``memory_search``, and ``memory_brief``. The
``BenchmarkResult`` / ``BenchmarkReport`` dataclasses are re-exported
so existing import paths keep working.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.maintenance.benchmark_runner import (
    BenchmarkReport,
    BenchmarkResult,
    measure,
)
from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.maintenance.integrity import run_integrity_audit
from agent_memory_lite.maintenance.quality_gate import run_quality_gate
from agent_memory_lite.storage.reader import search
from agent_memory_lite.vector_store.base import VectorStore

__all__ = ["BenchmarkReport", "BenchmarkResult", "run_memory_benchmarks"]


def run_memory_benchmarks(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    queries: list[str],
    runs: int = 3,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    max_context_tokens: int = 2500,
    thresholds_ms: dict[str, float] | None = None,
) -> BenchmarkReport:
    del embedding_provider
    thresholds = thresholds_ms or {}
    safe_runs = max(1, runs)
    safe_queries = queries or ["memory trust benchmark"]
    results: list[BenchmarkResult] = []

    results.append(
        measure(
            "sqlite_quick_check",
            lambda: conn.execute("PRAGMA quick_check").fetchone(),
            runs=safe_runs,
            threshold_ms=thresholds.get("sqlite_quick_check"),
        )
    )
    results.append(
        measure(
            "integrity_audit",
            lambda: run_integrity_audit(
                conn,
                workspace_id=workspace_id,
                vector_store=vector_store,
            ),
            runs=safe_runs,
            threshold_ms=thresholds.get("integrity_audit"),
        )
    )
    results.append(
        measure(
            "hygiene_report",
            lambda: run_hygiene_report(conn, workspace_id=workspace_id),
            runs=safe_runs,
            threshold_ms=thresholds.get("hygiene_report"),
        )
    )
    results.append(
        measure(
            "quality_gate",
            lambda: run_quality_gate(conn, workspace_id=workspace_id),
            runs=safe_runs,
            threshold_ms=thresholds.get("quality_gate"),
        )
    )

    def make_fts_search(query_text: str) -> Callable[[], object]:
        return lambda: search_chunks_fts(
            conn,
            workspace_id=workspace_id,
            query=query_text,
            limit=10,
        )

    def make_memory_search(query_text: str) -> Callable[[], object]:
        return lambda: search(
            conn,
            workspace_id=workspace_id,
            query=query_text,
            limit=10,
        )

    def make_memory_brief(query_text: str) -> Callable[[], object]:
        return lambda: compose_brief(
            conn,
            workspace_id=workspace_id,
            task=query_text,
            max_tokens=max_context_tokens,
        )

    for index, query in enumerate(safe_queries, start=1):
        results.append(
            measure(
                f"fts_search[{index}]",
                make_fts_search(query),
                runs=safe_runs,
                threshold_ms=thresholds.get("fts_search"),
            )
        )
        results.append(
            measure(
                f"memory_search[{index}]",
                make_memory_search(query),
                runs=safe_runs,
                threshold_ms=thresholds.get("memory_search"),
            )
        )
        results.append(
            measure(
                f"memory_brief[{index}]",
                make_memory_brief(query),
                runs=safe_runs,
                threshold_ms=thresholds.get("memory_brief"),
            )
        )
    status = "degraded" if any(result.status == "degraded" for result in results) else "ok"
    return BenchmarkReport(status=status, workspace_id=workspace_id, results=results)
