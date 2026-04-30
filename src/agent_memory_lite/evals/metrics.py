"""Eval metrics + report dataclass."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(slots=True)
class EvalReport:
    cases_run: int = 0
    cases_passed: int = 0
    retrieval_recall_at_10: float = 0.0
    retrieval_precision_at_10: float = 0.0
    stale_fact_rate: float = 0.0
    secret_leak_count: int = 0
    prompt_injection_failures: int = 0
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "cases_run": self.cases_run,
            "cases_passed": self.cases_passed,
            "retrieval_recall_at_10": round(self.retrieval_recall_at_10, 4),
            "retrieval_precision_at_10": round(self.retrieval_precision_at_10, 4),
            "stale_fact_rate": round(self.stale_fact_rate, 4),
            "secret_leak_count": self.secret_leak_count,
            "prompt_injection_failures": self.prompt_injection_failures,
            "failures": list(self.failures),
        }


def recall_at_k(retrieved: list[str], expected: list[str], *, k: int = 10) -> float:
    if not expected:
        return 1.0
    top = retrieved[:k]
    found = sum(1 for chunk in expected if chunk in top)
    return found / len(expected)


def precision_at_k(retrieved: list[str], expected: list[str], *, k: int = 10) -> float:
    if not retrieved:
        return 0.0
    top = retrieved[:k]
    if not top:
        return 0.0
    relevant = sum(1 for chunk in top if chunk in expected)
    return relevant / len(top)


def reciprocal_rank(retrieved: list[str], expected: list[str], *, k: int = 10) -> float:
    if not expected:
        return 1.0
    expected_set = set(expected)
    for index, item_id in enumerate(retrieved[:k], start=1):
        if item_id in expected_set:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved: list[str], expected: list[str], *, k: int = 10) -> float:
    if not expected:
        return 1.0
    expected_set = set(expected)
    top = retrieved[:k]
    dcg = 0.0
    for index, item_id in enumerate(top, start=1):
        if item_id in expected_set:
            dcg += 1.0 / _log2(index + 1)
    ideal_hits = min(len(expected_set), k)
    ideal_dcg = sum(1.0 / _log2(index + 1) for index in range(1, ideal_hits + 1))
    if ideal_dcg == 0:
        return 0.0
    return dcg / ideal_dcg


def hit_rate(found: list[str], expected: list[str]) -> float:
    if not expected:
        return 1.0
    found_set = set(found)
    return sum(1 for item_id in expected if item_id in found_set) / len(expected)


def _log2(value: int) -> float:
    return (
        float(value.bit_length() - 1)
        if value > 0 and value & (value - 1) == 0
        else math.log2(value)
    )
