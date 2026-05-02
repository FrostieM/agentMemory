from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.theory_writer import add_theory_evidence, write_theory
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import TheoryEvidenceKind, TheoryStatus
from agent_memory_lite.models.theories import TheoryEvidenceIn, TheoryIn
from agent_memory_lite.repositories.theories_repo import (
    get_theory,
    list_evidence_for_theory,
    list_theories,
)


def test_write_theory_round_trips_structured_research_fields(
    applied_conn: sqlite3.Connection,
) -> None:
    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="Source-flip favorites",
            domain="trading.paper.edge",
            claim="Source flips on tennis favorites may carry short-lived edge.",
            mechanism="The source wallet may react faster than market makers after favorite odds move.",
            predictions=[
                "edge decays within one poller interval",
                "underdog flips should not share it",
            ],
            validation_criteria=[
                "at least 100 settled trades",
                "positive net edge after fee assumptions",
            ],
            experiment_plan="Replay source-flip trades by sport and favorite side.",
            tags=["source-flip", "tennis", "favorite"],
            status=TheoryStatus.TESTING,
            confidence=0.35,
            importance=0.9,
        ),
    )

    stored = get_theory(applied_conn, theory.id)
    assert stored is not None
    assert stored.title == "Source-flip favorites"
    assert stored.predictions == [
        "edge decays within one poller interval",
        "underdog flips should not share it",
    ]
    assert stored.validation_criteria == [
        "at least 100 settled trades",
        "positive net edge after fee assumptions",
    ]
    assert stored.tags == ["source-flip", "tennis", "favorite"]
    assert stored.evidence_count == 0
    assert stored.evidence_strength == 0.0


def test_add_theory_evidence_and_query_by_tag(applied_conn: sqlite3.Connection) -> None:
    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="Tennis source-flip edge",
            claim="Tennis favorite source-flips deserve a separate replay bucket.",
            domain="trading.paper.edge",
            tags=["tennis", "source-flip"],
        ),
    )

    evidence = add_theory_evidence(
        applied_conn,
        TheoryEvidenceIn(
            workspace_id="default",
            theory_id=theory.id,
            kind=TheoryEvidenceKind.EXPERIMENT,
            summary="Replay query planned against server_20260427T105823.",
            artifact_path="research/snapshots/server_20260427T105823/research.duckdb",
            metrics={"planned": True},
        ),
    )

    hits = list_theories(applied_conn, workspace_id="default", query="tennis source-flip")
    assert [hit.id for hit in hits] == [theory.id]
    attached = list_evidence_for_theory(applied_conn, theory.id)
    assert attached[0].id == evidence.id
    assert attached[0].metrics == {"planned": True}
    updated = get_theory(applied_conn, theory.id)
    assert updated is not None
    assert updated.evidence_count == 1
    assert updated.evidence_strength == 0.0


def test_superseding_theory_archives_prior(applied_conn: sqlite3.Connection) -> None:
    first = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="old",
            claim="old claim",
        ),
    )
    second = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="new",
            claim="new claim",
            supersedes_theory_id=first.id,
        ),
    )

    archived = get_theory(applied_conn, first.id)
    assert archived is not None
    assert archived.status is TheoryStatus.SUPERSEDED
    assert second.supersedes_theory_id == first.id


def test_supersedes_must_exist_and_match_workspace(applied_conn: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        write_theory(
            applied_conn,
            TheoryIn(
                workspace_id="default",
                title="new",
                claim="new claim",
                supersedes_theory_id="th_missing",
            ),
        )

    other = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="other",
            title="other",
            claim="other claim",
        ),
    )
    with pytest.raises(ValidationError):
        write_theory(
            applied_conn,
            TheoryIn(
                workspace_id="default",
                title="new",
                claim="new claim",
                supersedes_theory_id=other.id,
            ),
        )


def test_dependent_decisions_must_exist_and_match_workspace(
    applied_conn: sqlite3.Connection,
) -> None:
    decision = write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="default",
            title="Use soft gate",
            decision_text="Run a softer paper gate for measurement.",
        ),
    )
    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="Soft gate improves learning",
            claim="A softer gate increases useful paper observations.",
            dependent_decision_ids=[decision.id],
        ),
    )
    assert theory.dependent_decision_ids == [decision.id]

    with pytest.raises(NotFoundError):
        write_theory(
            applied_conn,
            TheoryIn(
                workspace_id="default",
                title="missing decision",
                claim="bad link",
                dependent_decision_ids=["dec_missing"],
            ),
        )

    other = write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="other",
            title="other",
            decision_text="other workspace decision",
        ),
    )
    with pytest.raises(ValidationError):
        write_theory(
            applied_conn,
            TheoryIn(
                workspace_id="default",
                title="wrong workspace",
                claim="bad link",
                dependent_decision_ids=[other.id],
            ),
        )
