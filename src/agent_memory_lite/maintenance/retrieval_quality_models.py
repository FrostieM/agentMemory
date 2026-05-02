"""Result types + case loader for retrieval-quality evals.

Split out of ``retrieval_quality.py`` so the orchestrator stays under
the SLOC ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


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
