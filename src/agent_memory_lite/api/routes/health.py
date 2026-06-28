"""GET /health - liveness (shallow, default) + retrieval-integrity (deep).

Shallow mode (no query params) returns in milliseconds — the question is
"is the HTTP service actually serving?", not "is every retrieval index
intact?". ``?deep=true`` runs the 12-check audit which can take 30-60s
on a real workspace. Response shape is identical in both modes; shallow
mode fills the audit/feedback/review sections with ``not_checked`` stubs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import DbDep, SettingsDep, VectorStoreDep
from agent_memory_lite.api.routes.memory_status_queries import gather_degradation
from agent_memory_lite.maintenance.feedback_signal import feedback_signal_summary
from agent_memory_lite.maintenance.integrity import run_integrity_audit
from agent_memory_lite.repositories.vector_metadata_repo import provider_name_from_settings
from agent_memory_lite.retrieval.pending_review import load_pending_review
from agent_memory_lite.version import __version__

router = APIRouter()


class RetrievalIntegritySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    counts: dict[str, Any]
    failures: list[str]
    warnings: list[str]
    repair_hints: list[str]


class FeedbackSignalSummaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    total_rows: int
    unique_sources: int
    self_loop_ratio: float
    last_feedback_at: str | None


class PendingReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_reviews: int
    insight_reviews: int
    correction_reviews: int
    total: int


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    version: str
    db: str
    workspace_id: str
    embedding_backend: str
    embedding_model: str
    vector_backend: str
    llm_backend: str
    llm_model: str
    applied_migrations: list[str]
    # Fast-path degradation signal (M6/L4): open maintenance_events surfaced even
    # in shallow mode, so a degraded substrate no longer reports a flat "ok".
    open_maintenance_events: int = 0
    degraded: bool = False
    retrieval_integrity: RetrievalIntegritySummary
    feedback_signal: FeedbackSignalSummaryModel
    pending_review: PendingReviewModel


_SHALLOW_INTEGRITY_STUB = RetrievalIntegritySummary(
    status="not_checked",
    counts={},
    failures=[],
    warnings=[],
    repair_hints=["pass ?deep=true to /health to run the integrity audit"],
)
_SHALLOW_FEEDBACK_STUB = FeedbackSignalSummaryModel(
    enabled=False, total_rows=0, unique_sources=0, self_loop_ratio=0.0, last_feedback_at=None
)
_SHALLOW_REVIEW_STUB = PendingReviewModel(
    decision_reviews=0, insight_reviews=0, correction_reviews=0, total=0
)


def _base_response(settings: Any, versions: list[str]) -> dict[str, Any]:
    return {
        "version": __version__,
        "db": "ok",
        "workspace_id": settings.workspace_id,
        "embedding_backend": settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "vector_backend": settings.vector_backend,
        "llm_backend": settings.llm_backend,
        "llm_model": settings.llm_model,
        "applied_migrations": versions,
    }


@router.get("/health", response_model=HealthResponse)
def health(
    settings: SettingsDep,
    conn: DbDep,
    store: VectorStoreDep,
    deep: bool = False,
) -> HealthResponse:
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    versions = [str(row[0]) for row in rows]
    # Cheap open-maintenance_events read on BOTH paths so even shallow /health
    # reports a degraded substrate instead of a flat "ok" (M6/L4).
    degradation = gather_degradation(conn, settings.workspace_id)
    if not deep:
        return HealthResponse(
            status="degraded" if degradation.degraded else "ok",
            **_base_response(settings, versions),
            open_maintenance_events=degradation.open_maintenance_events,
            degraded=degradation.degraded,
            retrieval_integrity=_SHALLOW_INTEGRITY_STUB,
            feedback_signal=_SHALLOW_FEEDBACK_STUB,
            pending_review=_SHALLOW_REVIEW_STUB,
        )
    report = run_integrity_audit(
        conn,
        workspace_id=settings.workspace_id,
        vector_store=store,
        db_path=settings.db_path,
        expected_provider_name=provider_name_from_settings(
            embedding_backend=settings.embedding_backend,
            embedding_model=settings.embedding_model,
        ),
        expected_vector_backend=settings.vector_backend,
    )
    feedback = feedback_signal_summary(
        conn, workspace_id=settings.workspace_id, enabled=settings.feedback_ewma_enabled
    )
    review = load_pending_review(conn, workspace_id=settings.workspace_id)
    return HealthResponse(
        status="degraded" if (report.status == "degraded" or degradation.degraded) else "ok",
        **_base_response(settings, versions),
        open_maintenance_events=degradation.open_maintenance_events,
        degraded=degradation.degraded,
        retrieval_integrity=RetrievalIntegritySummary(
            status=report.status,
            counts=report.counts,
            failures=report.failures,
            warnings=report.warnings,
            repair_hints=report.repair_hints,
        ),
        feedback_signal=FeedbackSignalSummaryModel(**feedback.to_dict()),
        pending_review=PendingReviewModel(
            decision_reviews=review.decision_review_count,
            insight_reviews=review.insight_review_count,
            correction_reviews=review.correction_review_count,
            total=review.total,
        ),
    )
