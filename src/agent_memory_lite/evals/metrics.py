"""Eval metrics + report dataclass."""

from __future__ import annotations

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
