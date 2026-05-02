"""Conflict detector should not fire against archived / superseded rows.

A theory at ``status='archived'`` or ``status='superseded'`` is no
longer load-bearing and must not generate ``potential_conflict``
events when a new theory's tokens overlap it. Same goes for
decisions: only ``status='active'`` rows are valid comparison
targets.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion.conflict_detect import detect_conflicts
from agent_memory_lite.models.enums import MaintenanceEventStatus
from agent_memory_lite.repositories.maintenance_repo import list_maintenance_events


@pytest.fixture
def conflict_settings(settings_factory) -> Settings:
    return settings_factory(
        MEMORY_CONFLICT_DETECT_ENABLED="true",
        MEMORY_CONFLICT_DETECT_THRESHOLD="0.05",
    )


def _seed_theory(
    conn: sqlite3.Connection,
    *,
    theory_id: str,
    workspace_id: str,
    title: str,
    claim: str,
    status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO theories (
            id, workspace_id, title, domain, claim, predictions_json,
            validation_criteria_json, dependent_decision_ids_json, tags_json,
            status, source_episode_id, confidence, importance, evidence_count,
            evidence_strength, created_at, updated_at, last_tested_at,
            mechanism, experiment_plan, supersedes_theory_id
        ) VALUES (
            ?, ?, ?, 'd', ?, '[]', '[]', '[]', '[]',
            ?, NULL, 0.5, 0.5, 0, 0.0, '2026-01-01', '2026-01-01', NULL,
            NULL, NULL, NULL
        )
        """,
        (theory_id, workspace_id, title, claim, status),
    )
    conn.commit()


@pytest.mark.parametrize("buried_status", ["archived", "rejected", "superseded"])
def test_detect_conflicts_skips_buried_theories(
    applied_conn: sqlite3.Connection,
    conflict_settings: Settings,
    buried_status: str,
) -> None:
    workspace = f"conflict-detect-{buried_status}"
    _seed_theory(
        applied_conn,
        theory_id="th_old",
        workspace_id=workspace,
        title="Source-flip favorite tennis edge",
        claim="Tennis favorites carry short-lived edge after source flip.",
        status=buried_status,
    )
    _seed_theory(
        applied_conn,
        theory_id="th_new",
        workspace_id=workspace,
        title="Source-flip favorite tennis edge",
        claim="Tennis favorites carry short-lived edge after source flip.",
        status="testing",
    )
    matches = detect_conflicts(
        applied_conn,
        settings=conflict_settings,
        workspace_id=workspace,
        target_type="theory",
        target_id="th_new",
        target_text="Source-flip favorite tennis edge favorites carry short-lived",
    )
    assert matches == []
    events = list_maintenance_events(
        applied_conn,
        workspace_id=workspace,
        statuses=[MaintenanceEventStatus.OPEN],
    )
    assert all(event.kind != "potential_conflict" for event in events)


def test_detect_conflicts_still_fires_against_live_overlap(
    applied_conn: sqlite3.Connection, conflict_settings: Settings
) -> None:
    """Smoke-check that the heuristic still catches genuine overlap
    between two live theories so the archived-skip change didn't
    accidentally disable detection altogether."""
    workspace = "conflict-detect-live"
    _seed_theory(
        applied_conn,
        theory_id="th_live_a",
        workspace_id=workspace,
        title="Source-flip favorite tennis edge",
        claim="Tennis favorites carry short-lived edge after source flip.",
        status="testing",
    )
    _seed_theory(
        applied_conn,
        theory_id="th_live_b",
        workspace_id=workspace,
        title="Source-flip favorite tennis edge",
        claim="Tennis favorites carry short-lived edge after source flip.",
        status="proposed",
    )
    matches = detect_conflicts(
        applied_conn,
        settings=conflict_settings,
        workspace_id=workspace,
        target_type="theory",
        target_id="th_live_b",
        target_text="Source-flip favorite tennis edge favorites carry short-lived",
    )
    assert any(mid == "th_live_a" for mid, _score in matches)
