"""Per-case dispatch for the eval runner.

Split out of ``runner.py`` so each kind handler stays small.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.evals.metrics import EvalReport
from agent_memory_lite.evals.runner_research import run_research_context
from agent_memory_lite.evals.runner_retrieval import (
    run_retrieval,
    run_retrieval_context_quality,
)
from agent_memory_lite.evals.runner_security import (
    run_prompt_injection,
    run_redaction,
    run_trust_gating,
)
from agent_memory_lite.vector_store.base import VectorStore

_RetrievalFn = Callable[..., tuple[float, float, list[str]]]


def _record_retrieval(
    fn: _RetrievalFn,
    case: dict[str, Any],
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    report: EvalReport,
    recalls: list[float],
    precisions: list[float],
) -> None:
    recall, precision, failures = fn(
        conn,
        case,
        workspace_id,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    name = case.get("name", "?")
    recalls.append(recall)
    precisions.append(precision)
    if failures:
        report.failures.extend(f"{name}: {f}" for f in failures)
    else:
        report.cases_passed += 1


def process_case(  # noqa: PLR0912
    case: dict[str, Any],
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    report: EvalReport,
    recalls: list[float],
    precisions: list[float],
) -> None:
    kind = case.get("type", "retrieval")
    name = case.get("name", "?")
    if kind == "retrieval":
        _record_retrieval(
            run_retrieval,
            case,
            conn,
            workspace_id,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            report=report,
            recalls=recalls,
            precisions=precisions,
        )
    elif kind == "retrieval_context_quality":
        _record_retrieval(
            run_retrieval_context_quality,
            case,
            conn,
            workspace_id,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            report=report,
            recalls=recalls,
            precisions=precisions,
        )
    elif kind == "redaction":
        leaks = run_redaction(
            conn,
            case,
            workspace_id,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
        report.secret_leak_count += leaks
        if leaks == 0:
            report.cases_passed += 1
        else:
            report.failures.append(f"{name}: secret leaked")
    elif kind == "research_context":
        failures = run_research_context(
            conn,
            case,
            workspace_id,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
        if failures:
            report.failures.extend(f"{name}: {failure}" for failure in failures)
        else:
            report.cases_passed += 1
    elif kind == "trust_gating":
        if run_trust_gating(case):
            report.cases_passed += 1
        else:
            report.failures.append(f"{name}: trust gating outcome mismatched")
    elif kind == "prompt_injection":
        failures_int = run_prompt_injection(case)
        report.prompt_injection_failures += failures_int
        if failures_int == 0:
            report.cases_passed += 1
        else:
            report.failures.append(f"{name}: prompt injection slipped")
    else:
        report.failures.append(f"{name}: unknown case type {kind!r}")
