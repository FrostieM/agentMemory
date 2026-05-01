"""Live retrieval quality evals for trusted project memory.

The normal eval harness runs against synthetic temporary databases. This module
checks the real selected memory DB: known queries must surface known objects in
`memory_get_context`, with the expected retrieval sources when specified.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.evals.metrics import hit_rate, ndcg_at_k, recall_at_k, reciprocal_rank
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.vector_store.base import VectorStore


@dataclass(frozen=True, slots=True)
class RetrievalQualityCase:
    name: str
    query: str
    expected_ids: list[str] = field(default_factory=list)
    expected_context_ids: list[str] = field(default_factory=list)
    expected_object_titles: list[str] = field(default_factory=list)
    expected_substrings: list[str] = field(default_factory=list)
    expected_sections: list[str] = field(default_factory=list)
    expected_sources: list[str] = field(default_factory=list)
    min_render_level: str | None = None
    expected_omissions_absent: bool = False
    top_k: int = 10
    max_tokens: int = 2500

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> RetrievalQualityCase:
        name = str(data.get("name") or data.get("query") or "unnamed")
        query = str(data["query"])
        return cls(
            name=name,
            query=query,
            expected_ids=[str(item) for item in data.get("expected_ids", [])],
            expected_context_ids=[
                str(item)
                for item in data.get(
                    "expected_context_ids",
                    data.get("expected_object_ids", []),
                )
            ],
            expected_object_titles=[str(item) for item in data.get("expected_object_titles", [])],
            expected_substrings=[str(item) for item in data.get("expected_substrings", [])],
            expected_sections=[str(item) for item in data.get("expected_sections", [])],
            expected_sources=[str(item) for item in data.get("expected_sources", [])],
            min_render_level=(
                str(data["min_render_level"]) if data.get("min_render_level") else None
            ),
            expected_omissions_absent=bool(data.get("expected_omissions_absent", False)),
            top_k=max(1, int(data.get("top_k", 10))),
            max_tokens=max(200, int(data.get("max_tokens", 2500))),
        )


@dataclass(frozen=True, slots=True)
class RetrievalQualityResult:
    name: str
    status: str
    query: str
    top_k: int
    expected_ids: list[str]
    matched_ids: list[str]
    retrieved_ids: list[str]
    expected_context_ids: list[str]
    matched_context_ids: list[str]
    expected_object_titles: list[str]
    matched_object_titles: list[str]
    expected_sources: list[str]
    source_map: dict[str, list[str]]
    render_levels: dict[str, str]
    budget_diagnostics: dict[str, Any]
    metrics: dict[str, float]
    failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "query": self.query,
            "top_k": self.top_k,
            "expected_ids": self.expected_ids,
            "matched_ids": self.matched_ids,
            "retrieved_ids": self.retrieved_ids,
            "expected_context_ids": self.expected_context_ids,
            "matched_context_ids": self.matched_context_ids,
            "expected_object_titles": self.expected_object_titles,
            "matched_object_titles": self.matched_object_titles,
            "expected_sources": self.expected_sources,
            "source_map": self.source_map,
            "render_levels": self.render_levels,
            "budget_diagnostics": self.budget_diagnostics,
            "metrics": self.metrics,
            "failures": self.failures,
        }


@dataclass(frozen=True, slots=True)
class RetrievalQualityReport:
    status: str
    workspace_id: str
    cases_run: int
    cases_passed: int
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    context_hit_rate: float
    failures: list[str]
    results: list[RetrievalQualityResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "cases_run": self.cases_run,
            "cases_passed": self.cases_passed,
            "recall_at_k": round(self.recall_at_k, 4),
            "mrr": round(self.mrr, 4),
            "ndcg_at_k": round(self.ndcg_at_k, 4),
            "context_hit_rate": round(self.context_hit_rate, 4),
            "failures": self.failures,
            "results": [result.to_dict() for result in self.results],
        }


def load_retrieval_quality_cases(path: Path) -> list[RetrievalQualityCase]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(parsed, list):
        raise ValueError(f"{path} must contain a YAML list of retrieval quality cases")
    return [RetrievalQualityCase.from_mapping(dict(item)) for item in parsed]


_RENDER_LEVEL_RANK = {"none": 0, "stub": 1, "summary": 2, "full": 3}


def _render_rank(level: str | None) -> int:
    return _RENDER_LEVEL_RANK.get(str(level or "none"), 0)


def _render_levels_from_diagnostics(diagnostics: dict[str, Any]) -> dict[str, str]:
    sections = diagnostics.get("sections", [])
    if not isinstance(sections, list):
        return {}
    levels: dict[str, str] = {}
    for item in sections:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name:
            levels[name] = str(item.get("render_level") or "none")
    return levels


def _run_case(  # noqa: PLR0912
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
    render_levels = _render_levels_from_diagnostics(built.budget_diagnostics)
    failures: list[str] = []

    missing_ids = [item for item in case.expected_ids if item not in retrieved_ids]
    if missing_ids:
        failures.append(f"missing expected ids in top {case.top_k}: {missing_ids}")
    missing_context_ids = [
        item for item in case.expected_context_ids if item not in matched_context_ids
    ]
    if missing_context_ids:
        failures.append(f"missing expected context ids: {missing_context_ids}")

    for expected_source in case.expected_sources:
        source_ok = False
        if expected_source == "both":
            source_ok = any(
                {"fts", "vector"}.issubset(set(source_map.get(expected_id, [])))
                for expected_id in matched_ids
            )
        if case.expected_ids:
            source_ok = source_ok or any(
                expected_source in source_map.get(expected_id, []) for expected_id in matched_ids
            )
        else:
            source_ok = any(expected_source in sources for sources in source_map.values())
        if not source_ok:
            failures.append(f"missing expected retrieval source {expected_source!r}")

    for section in case.expected_sections:
        if f"<{section}" not in built.text:
            failures.append(f"missing context section <{section}>")

    missing_titles = [
        item for item in case.expected_object_titles if item not in matched_object_titles
    ]
    if missing_titles:
        failures.append(f"missing expected object titles: {missing_titles}")

    if case.min_render_level:
        checked_sections = case.expected_sections or list(render_levels)
        weak_sections = [
            section
            for section in checked_sections
            if _render_rank(render_levels.get(section)) < _render_rank(case.min_render_level)
        ]
        if weak_sections:
            failures.append(f"sections below render level {case.min_render_level}: {weak_sections}")

    if case.expected_omissions_absent and built.budget_diagnostics.get("omissions"):
        failures.append("unexpected context omissions under sentinel budget")

    for needle in case.expected_substrings:
        if needle not in built.text:
            failures.append(f"missing context substring {needle!r}")

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
            "reciprocal_rank": reciprocal_rank(
                retrieved_ids,
                case.expected_ids,
                k=case.top_k,
            ),
            "ndcg_at_k": ndcg_at_k(retrieved_ids, case.expected_ids, k=case.top_k),
            "context_hit_rate": hit_rate(matched_context_ids, expected_context_ids),
        },
        failures=failures,
    )


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
            result = _run_case(
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
        context_hit_rate=sum(result.metrics["context_hit_rate"] for result in results)
        / denominator,
        failures=failures,
        results=results,
    )
