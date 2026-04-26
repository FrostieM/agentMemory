from __future__ import annotations

from agent_memory_lite.evals.metrics import (
    EvalReport,
    precision_at_k,
    recall_at_k,
)


def test_recall_full() -> None:
    assert recall_at_k(["a", "b", "c"], ["a", "b"], k=10) == 1.0


def test_recall_partial() -> None:
    assert recall_at_k(["a", "x", "y"], ["a", "b"], k=10) == 0.5


def test_recall_empty_expected() -> None:
    assert recall_at_k(["a"], [], k=10) == 1.0


def test_precision_full() -> None:
    assert precision_at_k(["a", "b"], ["a", "b"], k=10) == 1.0


def test_precision_no_overlap() -> None:
    assert precision_at_k(["x", "y"], ["a", "b"], k=10) == 0.0


def test_precision_empty_retrieved() -> None:
    assert precision_at_k([], ["a"], k=10) == 0.0


def test_report_dict_round_trips() -> None:
    report = EvalReport(
        cases_run=2,
        cases_passed=1,
        retrieval_recall_at_10=0.85,
        retrieval_precision_at_10=0.65,
        secret_leak_count=0,
    )
    data = report.as_dict()
    assert data["cases_run"] == 2
    assert data["retrieval_recall_at_10"] == 0.85
