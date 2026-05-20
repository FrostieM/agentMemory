"""Round-2 audit #3: ``memory_record_with_evidence`` atomicity.

The compound write chains ingest_episode + write_decision + optional
link_capability. Pre-fix each inner writer committed on its own — so a
failure in step 2 left the episode from step 1 committed: orphan
episode pointing nowhere + half-state in the DB.

Post-fix the MCP path wraps the whole sequence in ``with_tx(conn)``;
the inner writers' BEGINs become SAVEPOINTs that roll back to the
outer transaction on failure. The HTTP route gets the same treatment
in ``api/routes/record_compound.py``.

Caveat: vector embeddings inside ``ingest_episode`` flush to LanceDB
after the SQLite SAVEPOINT release, so a later SQLite rollback can
leave orphan vectors. Recoverable via ``scripts/reindex_vectors.py``.
This test only locks the SQLite-side atomicity (the hard part).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.api.agent_context import (
    reset_current_agent_id,
    set_current_agent_id,
)
from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.mcp import tools_compound


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_connection(tmp_path / "atomic.db")
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clear_agent() -> Iterator[None]:
    reset_current_agent_id()
    set_current_agent_id("atomic-test")
    yield
    reset_current_agent_id()


def _episode_count(conn: sqlite3.Connection, workspace_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM episodes WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()[0]


def _decision_count(conn: sqlite3.Connection, workspace_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()[0]


def test_decision_failure_rolls_back_episode(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject a write_decision failure AFTER ingest_episode commits its
    SAVEPOINT. With the outer with_tx the episode must roll back too."""
    assert _episode_count(db, "alpha") == 0
    assert _decision_count(db, "alpha") == 0

    def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated decision-writer failure")

    monkeypatch.setattr(tools_compound, "write_decision", boom)

    with pytest.raises(RuntimeError, match="simulated decision-writer failure"):
        tools_compound.memory_record_with_evidence(
            conn=db,
            embedding_provider=None,
            vector_store=None,
            payload={
                "workspace_id": "alpha",
                "evidence_text": "Pre-decision evidence text.",
                "decision_title": "Will never persist",
                "decision_text": "This decision write is the one that fails.",
            },
        )

    # The episode from step 1 must NOT remain. Pre-fix it would: each
    # writer ran its own with_tx so step 1 committed before step 2 blew
    # up. Post-fix the outer with_tx wraps both and rolls them back as
    # one unit.
    assert _episode_count(db, "alpha") == 0, "episode from step 1 must roll back when step 2 fails"
    assert _decision_count(db, "alpha") == 0


def test_link_capability_failure_rolls_back_episode_and_decision(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 3 (link_capability) is optional but, when invoked, a
    failure inside it must also drag steps 1+2 back."""
    assert _episode_count(db, "beta") == 0

    def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated link-capability failure")

    monkeypatch.setattr(tools_compound, "link_capability", boom)

    with pytest.raises(RuntimeError, match="simulated link-capability failure"):
        tools_compound.memory_record_with_evidence(
            conn=db,
            embedding_provider=None,
            vector_store=None,
            payload={
                "workspace_id": "beta",
                "evidence_text": "Evidence before optional link.",
                "decision_title": "Decision that will be rolled back",
                "decision_text": "Step 1 + 2 succeed; step 3 fails.",
                # Triggering step 3:
                "capability_type": "skill",
                "capability_name": "fake-skill-name",
                "capability_relation": "method",
            },
        )

    assert _episode_count(db, "beta") == 0, (
        "episode + decision must roll back when step 3 (link) fails"
    )
    assert _decision_count(db, "beta") == 0


def test_happy_path_persists_all_three(db: sqlite3.Connection) -> None:
    """No injected failure: all three steps land, txn commits cleanly."""
    response = tools_compound.memory_record_with_evidence(
        conn=db,
        embedding_provider=None,
        vector_store=None,
        payload={
            "workspace_id": "gamma",
            "evidence_text": "Real evidence text for happy-path test.",
            "decision_title": "Happy-path decision",
            "decision_text": "Persists end-to-end.",
        },
    )
    assert response["decision_status"] == "active"
    assert _episode_count(db, "gamma") == 1
    assert _decision_count(db, "gamma") == 1
