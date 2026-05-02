from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.research_writer import (
    add_experiment_result,
    distill_insight,
    register_snapshot,
    update_insight,
    upsert_domain_concept,
    write_experiment,
)
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.models.enums import (
    ConceptKind,
    ExperimentStatus,
    InsightStatus,
    InsightType,
    TheoryEvidenceKind,
    TheoryStatus,
)
from agent_memory_lite.models.research import (
    DomainConceptIn,
    ExperimentIn,
    ExperimentResultIn,
    MemorySnapshotIn,
    ResearchInsightIn,
    ResearchInsightUpdateIn,
)
from agent_memory_lite.models.theories import TheoryIn
from agent_memory_lite.repositories.research_repo import (
    get_experiment,
    list_concepts,
    list_insights,
)
from agent_memory_lite.repositories.theories_repo import get_theory, list_evidence_for_theory


def test_snapshot_experiment_result_updates_theory_confidence(
    applied_conn: sqlite3.Connection,
) -> None:
    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="Source-flip favorites",
            claim="Tennis favorite source-flips may have edge.",
            status=TheoryStatus.PROPOSED,
            confidence=0.4,
        ),
    )
    snapshot = register_snapshot(
        applied_conn,
        MemorySnapshotIn(
            workspace_id="default",
            snapshot_key="server_20260427T105823",
            title="VPS snapshot before reset",
            duckdb_path="research/snapshots/server_20260427T105823/research.duckdb",
            table_counts={"bot_trade_log": 226057},
            total_rows=499141,
        ),
    )
    experiment = write_experiment(
        applied_conn,
        ExperimentIn(
            workspace_id="default",
            theory_id=theory.id,
            snapshot_id=snapshot.id,
            title="Replay favorite source-flips",
            hypothesis="Favorites outperform underdogs after source-flip.",
            success_criteria={"min_trades": 100, "net_edge_bps_gt": 0},
            priority=0.9,
        ),
    )

    result = add_experiment_result(
        applied_conn,
        ExperimentResultIn(
            workspace_id="default",
            experiment_id=experiment.id,
            kind=TheoryEvidenceKind.SUPPORTING,
            summary="Initial replay supports the favorite-only cohort.",
            metrics={"trades": 144, "net_edge_bps": 31.2},
            confidence=0.8,
            artifact_path="reports/research/source_flip_favorites.md",
        ),
    )

    stored_experiment = get_experiment(applied_conn, experiment.id)
    assert stored_experiment is not None
    assert stored_experiment.status is ExperimentStatus.COMPLETED
    assert result.metrics["experiment_id"] == experiment.id
    updated_theory = get_theory(applied_conn, theory.id)
    assert updated_theory is not None
    assert updated_theory.confidence > theory.confidence
    assert updated_theory.status is TheoryStatus.TESTING
    evidence = list_evidence_for_theory(applied_conn, theory.id)
    assert evidence[0].summary == "Initial replay supports the favorite-only cohort."
    assert evidence[0].metrics["experiment_result_id"] == result.id


def test_refuting_result_creates_contradiction_insight(applied_conn: sqlite3.Connection) -> None:
    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="Sparse opens are only a runtime bug",
            claim="Paper opens are sparse because the trader path is broken.",
            status=TheoryStatus.SUPPORTED,
            confidence=0.72,
        ),
    )
    experiment = write_experiment(
        applied_conn,
        ExperimentIn(
            workspace_id="default",
            theory_id=theory.id,
            title="Admission-gate audit",
            hypothesis="Selector/admission gates explain sparse opens.",
        ),
    )

    add_experiment_result(
        applied_conn,
        ExperimentResultIn(
            workspace_id="default",
            experiment_id=experiment.id,
            kind=TheoryEvidenceKind.REFUTING,
            summary="Trader path is healthy; selector gates block most candidates.",
            metrics={"queue_selected": 0, "paper_opened": 0},
            confidence=0.75,
        ),
    )

    updated = get_theory(applied_conn, theory.id)
    assert updated is not None
    assert updated.status is TheoryStatus.WEAKENED
    insights = list_insights(
        applied_conn,
        workspace_id="default",
        query="selector gates",
        statuses=[InsightStatus.NEW],
    )
    assert insights
    assert insights[0].insight_type.value == "contradiction"
    assert insights[0].target_id == theory.id


def test_concept_upsert_reuses_name_per_workspace(applied_conn: sqlite3.Connection) -> None:
    first = upsert_domain_concept(
        applied_conn,
        DomainConceptIn(
            workspace_id="default",
            name="paper-open-rate",
            kind=ConceptKind.METRIC,
            definition="Share of selected candidates that become paper positions.",
            tags=["trading-bot", "paper"],
        ),
    )
    second = upsert_domain_concept(
        applied_conn,
        DomainConceptIn(
            workspace_id="default",
            name="paper-open-rate",
            kind=ConceptKind.METRIC,
            definition="Paper positions opened divided by selector-approved candidates.",
            aliases=["open-rate"],
            confidence=0.9,
        ),
    )

    assert second.id == first.id
    concepts = list_concepts(applied_conn, workspace_id="default", query="open-rate")
    assert [concept.id for concept in concepts] == [first.id]
    assert (
        concepts[0].definition == "Paper positions opened divided by selector-approved candidates."
    )


def test_update_insight_links_existing_research_item(applied_conn: sqlite3.Connection) -> None:
    insight = distill_insight(
        applied_conn,
        ResearchInsightIn(
            workspace_id="default",
            insight_type=InsightType.RISK,
            summary="Insight exists but has not been linked into the reasoning graph.",
            proposed_action="Attach the insight to the theory it supports.",
            confidence=0.8,
        ),
    )

    updated = update_insight(
        applied_conn,
        ResearchInsightUpdateIn(
            workspace_id="default",
            insight_id=insight.id,
            target_type="theory",
            target_id="th_example",
            status=InsightStatus.ACCEPTED,
        ),
    )

    assert updated.id == insight.id
    assert updated.target_type == "theory"
    assert updated.target_id == "th_example"
    assert updated.status is InsightStatus.ACCEPTED
    insights = list_insights(applied_conn, workspace_id="default", query="th_example")
    assert [item.id for item in insights] == [insight.id]
