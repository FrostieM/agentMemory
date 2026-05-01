"""POST /memory/run_evals."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    EmbeddingProviderDep,
    SettingsDep,
    VectorStoreDep,
    ensure_workspace_writable,
)
from agent_memory_lite.api.schemas.evals import RunEvalsRequest, RunEvalsResponse
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.evals.runner import run_evals

router = APIRouter()


@router.post("/memory/run_evals", response_model=RunEvalsResponse)
def run_evals_route(
    body: RunEvalsRequest,
    settings: SettingsDep,
    provider: EmbeddingProviderDep,
    store: VectorStoreDep,
) -> RunEvalsResponse:
    workspace_id = body.workspace_id or settings.workspace_id
    ensure_workspace_writable(workspace_id, settings)

    @contextmanager
    def _conn_factory() -> Iterator[sqlite3.Connection]:
        conn = open_connection(":memory:")
        try:
            apply_migrations(conn)
            yield conn
        finally:
            close_connection(conn)

    report = run_evals(
        _conn_factory,
        workspace_id=workspace_id,
        embedding_provider=provider,
        vector_store=store,
    )
    return RunEvalsResponse(
        cases_run=report.cases_run,
        cases_passed=report.cases_passed,
        retrieval_recall_at_10=report.retrieval_recall_at_10,
        retrieval_precision_at_10=report.retrieval_precision_at_10,
        stale_fact_rate=report.stale_fact_rate,
        secret_leak_count=report.secret_leak_count,
        prompt_injection_failures=report.prompt_injection_failures,
        failures=list(report.failures),
    )
