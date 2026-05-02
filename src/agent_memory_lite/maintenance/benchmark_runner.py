"""Timing primitive + result types for the memory benchmark suite.

Split out of ``benchmark.py`` so the orchestrator stays small and the
core ``_measure`` helper plus the dataclasses can be reused by future
benchmark suites without the orchestrator's heavy import surface.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from statistics import mean
from typing import Any


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


def measure(
    name: str,
    fn: Callable[[], object],
    *,
    runs: int,
    threshold_ms: float | None,
) -> BenchmarkResult:
    """Run ``fn`` ``runs`` times and report mean/min/max/p95 in ms."""
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
