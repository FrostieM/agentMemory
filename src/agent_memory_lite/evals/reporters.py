"""Eval report serialization."""

from __future__ import annotations

import json
from pathlib import Path

from agent_memory_lite.evals.metrics import EvalReport


def to_json(report: EvalReport) -> str:
    return json.dumps(report.as_dict(), indent=2, sort_keys=True)


def to_console(report: EvalReport) -> str:
    lines = [
        f"cases_run                  : {report.cases_run}",
        f"cases_passed               : {report.cases_passed}",
        f"retrieval_recall@10        : {report.retrieval_recall_at_10:.4f}",
        f"retrieval_precision@10     : {report.retrieval_precision_at_10:.4f}",
        f"stale_fact_rate            : {report.stale_fact_rate:.4f}",
        f"secret_leak_count          : {report.secret_leak_count}",
        f"prompt_injection_failures  : {report.prompt_injection_failures}",
    ]
    if report.failures:
        lines.append("failures:")
        lines.extend(f"  - {failure}" for failure in report.failures)
    return "\n".join(lines)


def write_report(report: EvalReport, path: Path) -> None:
    path.write_text(to_json(report), encoding="utf-8")
