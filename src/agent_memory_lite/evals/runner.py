"""Eval case runner.

YAML cases drive a small in-memory exercise of the service:

- `retrieval` cases ingest one or more episodes, run `get_context`, and assert
  that the expected chunk ids appear in the top-K (recall + precision).
- `redaction` cases ingest one episode containing a known secret and assert
  the secret literal does not appear in the resulting chunk text or the
  rendered context.
- `trust_gating` cases declare a candidate and assert the trust gate matches
  the expected outcome.

Per-case dispatch lives in ``runner_dispatch.py``; per-kind helpers
live in ``runner_retrieval.py`` / ``runner_research.py`` /
``runner_security.py``.
"""

from __future__ import annotations

import importlib.resources
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.evals.metrics import EvalReport
from agent_memory_lite.evals.runner_dispatch import process_case
from agent_memory_lite.vector_store.base import VectorStore

FIXTURES_PACKAGE = "agent_memory_lite.evals.fixtures"


def _load_yaml_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text) or []
    if not isinstance(parsed, list):
        raise ValueError(f"{path} must contain a YAML list of eval cases")
    return parsed


def load_default_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    package_root = importlib.resources.files(FIXTURES_PACKAGE)
    yaml_entries = [path for path in package_root.iterdir() if path.name.endswith(".yaml")]
    yaml_entries.sort(key=lambda entry: entry.name)
    for entry in yaml_entries:
        with importlib.resources.as_file(entry) as concrete_path:
            cases.extend(_load_yaml_file(concrete_path))
    return cases


def run_evals(
    conn_factory: Callable[[], AbstractContextManager[sqlite3.Connection]],
    *,
    workspace_id: str,
    cases: Iterable[dict[str, Any]] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> EvalReport:
    materialized = list(cases) if cases is not None else load_default_cases()
    report = EvalReport()
    recalls: list[float] = []
    precisions: list[float] = []

    for case in materialized:
        report.cases_run += 1
        try:
            with conn_factory() as conn:
                process_case(
                    case,
                    conn,
                    workspace_id,
                    embedding_provider=embedding_provider,
                    vector_store=vector_store,
                    report=report,
                    recalls=recalls,
                    precisions=precisions,
                )
        except Exception as exc:
            report.failures.append(f"{case.get('name', '?')}: error {exc!s}")

    if recalls:
        report.retrieval_recall_at_10 = sum(recalls) / len(recalls)
    if precisions:
        report.retrieval_precision_at_10 = sum(precisions) / len(precisions)
    return report
