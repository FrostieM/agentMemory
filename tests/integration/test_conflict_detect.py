"""Integration: conflict_detect surfaces overlap warnings on decision/theory writes."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import TheoryStatus
from agent_memory_lite.models.theories import TheoryIn
from agent_memory_lite.repositories.maintenance_repo import list_maintenance_events

pytestmark = pytest.mark.integration


def _settings(enabled: bool = True, threshold: float = 0.4) -> Settings:
    return Settings(
        MEMORY_CONFLICT_DETECT_ENABLED="1" if enabled else "0",
        MEMORY_CONFLICT_DETECT_THRESHOLD=str(threshold),
    )


def test_conflict_detect_flags_similar_decisions(applied_conn: sqlite3.Connection) -> None:
    settings = _settings()
    write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="qa",
            title="Disable noisy heap watchdog timer",
            decision_text="Turn off the heap watchdog timer in production logging.",
        ),
        settings=settings,
    )
    write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="qa",
            title="Disable heap watchdog timer everywhere",
            decision_text="Stop running heap watchdog timer in production logging.",
        ),
        settings=settings,
    )
    events = list_maintenance_events(applied_conn, workspace_id="qa", limit=20)
    conflicts = [e for e in events if e.kind == "potential_conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].target_type == "decision"


def test_conflict_detect_off_writes_no_event(applied_conn: sqlite3.Connection) -> None:
    settings = _settings(enabled=False)
    write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="qa",
            title="Use offline replay before live policy changes",
            decision_text="Run replay before live changes.",
        ),
        settings=settings,
    )
    write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="qa",
            title="Use offline replay before live policy changes",
            decision_text="Run replay before live changes.",
        ),
        settings=settings,
    )
    events = list_maintenance_events(applied_conn, workspace_id="qa", limit=20)
    assert all(e.kind != "potential_conflict" for e in events)


def test_conflict_detect_flags_similar_theories(applied_conn: sqlite3.Connection) -> None:
    settings = _settings()
    write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="qa",
            title="Source-flip favourites carry short-lived edge",
            domain="trading",
            claim="Source-flip trades on tennis favourites carry short-lived edge.",
            status=TheoryStatus.PROPOSED,
        ),
        settings=settings,
    )
    write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="qa",
            title="Source-flip favourites have small edge",
            domain="trading",
            claim="Source-flip trades on tennis favourites have small short-lived edge.",
            status=TheoryStatus.PROPOSED,
        ),
        settings=settings,
    )
    events = list_maintenance_events(applied_conn, workspace_id="qa", limit=20)
    conflicts = [e for e in events if e.kind == "potential_conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].target_type == "theory"
