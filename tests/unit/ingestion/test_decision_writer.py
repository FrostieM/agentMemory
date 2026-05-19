from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import DecisionStatus
from agent_memory_lite.repositories.decisions_repo import (
    get_decision,
    list_active_decisions,
)


def _decision(**overrides: object) -> DecisionIn:
    payload: dict[str, object] = {
        "workspace_id": "default",
        "title": "Use SQLite + LanceDB",
        "decision_text": "Lite memory uses SQLite + LanceDB.",
        "rationale": "no Docker available",
    }
    payload.update(overrides)
    return DecisionIn(**payload)


def test_write_decision_persists_active(applied_conn: sqlite3.Connection) -> None:
    decision = write_decision(applied_conn, _decision())
    assert decision.status == DecisionStatus.ACTIVE
    assert decision.valid_to is None
    assert decision.id.startswith("dec_")


def test_supersedes_chain_closes_prior(applied_conn: sqlite3.Connection) -> None:
    first = write_decision(applied_conn, _decision(title="v1"))
    second = write_decision(
        applied_conn,
        _decision(title="v2", supersedes_decision_id=first.id),
    )
    refreshed_first = get_decision(applied_conn, first.id)
    assert refreshed_first is not None
    assert refreshed_first.status == DecisionStatus.SUPERSEDED
    assert refreshed_first.valid_to is not None
    actives = list_active_decisions(applied_conn, "default")
    active_ids = {d.id for d in actives}
    assert second.id in active_ids
    assert first.id not in active_ids


def test_supersedes_unknown_decision_raises(applied_conn: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        write_decision(applied_conn, _decision(supersedes_decision_id="dec_missing"))


def test_supersedes_cross_workspace_rejected(applied_conn: sqlite3.Connection) -> None:
    first = write_decision(applied_conn, _decision(workspace_id="other"))
    with pytest.raises(ValidationError):
        write_decision(
            applied_conn,
            _decision(workspace_id="default", supersedes_decision_id=first.id),
        )


def test_list_active_decisions_can_rank_by_query_and_limit(
    applied_conn: sqlite3.Connection,
) -> None:
    sqlite_decision = write_decision(
        applied_conn,
        _decision(
            title="Use SQLite for storage",
            decision_text="SQLite is the default durable store.",
            importance=0.5,
        ),
    )
    write_decision(
        applied_conn,
        _decision(
            title="Use HTTP health checks",
            decision_text="Health checks should stay lightweight.",
            importance=1.0,
        ),
    )

    matches = list_active_decisions(applied_conn, "default", query="SQLite durable", limit=1)

    assert [item.id for item in matches] == [sqlite_decision.id]


def test_write_decision_redacts_secrets_in_text_fields(
    applied_conn: sqlite3.Connection,
) -> None:
    """v3.0.0-final invariant: rationale + title + decision_text never
    persist cleartext secrets. The same RedactionPipeline that protects
    episode raw_text runs on every writer."""
    fake_key = "sk-proj-abcd1234efgh5678ijkl9012mnop3456qrst7890uvwx1234yz"
    decision = write_decision(
        applied_conn,
        _decision(
            title=f"Rotate api key {fake_key}",
            decision_text=f"Replace {fake_key} with rotated credential.",
            rationale=f"Old token {fake_key} was leaked in a screenshot.",
        ),
    )
    stored = get_decision(applied_conn, decision.id)
    assert stored is not None
    # Secret never appears in any persisted field.
    assert fake_key not in stored.title
    assert fake_key not in stored.decision_text
    assert fake_key not in (stored.rationale or "")
    # Redaction markers DO appear, proving redact() ran.
    assert "REDACTED" in stored.title
