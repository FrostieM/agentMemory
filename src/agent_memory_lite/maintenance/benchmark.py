"""Small performance benchmark suite for memory trust operations."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from statistics import mean
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.maintenance.integrity import run_integrity_audit
from agent_memory_lite.maintenance.quality_gate import run_quality_gate
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.vector_store.base import VectorStore


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    name: str
    runs: int
    mean_ms: float
    min_ms: float
    max_ms: float
    p95_ms: float
    threshold_ms: float | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "runs": self.runs,
            "mean_ms": round(self.mean_ms, 3),
            "min_ms": round(self.min_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "threshold_ms": self.threshold_ms,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    status: str
    workspace_id: str
    results: list[BenchmarkResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "results": [result.to_dict() for result in self.results],
        }


def _measure(
    name: str,
    fn: Callable[[], object],
    *,
    runs: int,
    threshold_ms: float | None,
) -> BenchmarkResult:
    durations: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        durations.append((time.perf_counter() - started) * 1000.0)
    ordered = sorted(durations)
    p95_index = max(0, min(len(ordered) - 1, ceil(len(ordered) * 0.95) - 1))
    p95 = ordered[p95_index]
    status = "ok"
    if threshold_ms is not None and p95 > threshold_ms:
        status = "degraded"
    return BenchmarkResult(
        name=name,
        runs=runs,
        mean_ms=mean(durations),
        min_ms=min(durations),
        max_ms=max(durations),
        p95_ms=p95,
        threshold_ms=threshold_ms,
        status=status,
    )


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
    thresholds = thresholds_ms or {}
    safe_runs = max(1, runs)
    safe_queries = queries or ["memory trust benchmark"]
    results: list[BenchmarkResult] = []

    results.append(
        _measure(
            "sqlite_quick_check",
            lambda: conn.execute("PRAGMA quick_check").fetchone(),
            runs=safe_runs,
            threshold_ms=thresholds.get("sqlite_quick_check"),
        )
    )
    results.append(
        _measure(
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
        _measure(
            "hygiene_report",
            lambda: run_hygiene_report(conn, workspace_id=workspace_id),
            runs=safe_runs,
            threshold_ms=thresholds.get("hygiene_report"),
        )
    )
    results.append(
        _measure(
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

    def make_get_context(query_text: str) -> Callable[[], object]:
        return lambda: build_context(
            conn,
            RetrievalQuery(
                workspace_id=workspace_id,
                query=query_text,
                max_tokens=max_context_tokens,
            ),
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )

    for index, query in enumerate(safe_queries, start=1):
        results.append(
            _measure(
                f"fts_search[{index}]",
                make_fts_search(query),
                runs=safe_runs,
                threshold_ms=thresholds.get("fts_search"),
            )
        )
        results.append(
            _measure(
                f"get_context[{index}]",
                make_get_context(query),
                runs=safe_runs,
                threshold_ms=thresholds.get("get_context"),
            )
        )
    status = "degraded" if any(result.status == "degraded" for result in results) else "ok"
    return BenchmarkReport(status=status, workspace_id=workspace_id, results=results)
