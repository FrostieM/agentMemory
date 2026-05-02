"""Regression: ``pinned`` flag must round-trip through row→model.

The earlier defensive check used ``"pinned" in row`` to decide
whether the column was present. On ``sqlite3.Row`` that operator
checks VALUES, not column names, so a row with ``pinned=1`` would
read as missing and the model would default to ``False``. The crash
test caught it via the UI: every decision came back as ``pinned:
False`` even after a successful POST /memory/pin.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.repositories.core_memory_repo import (
    _row_to_core,
    upsert_core_memory_row,
)
from agent_memory_lite.repositories.decisions_repo import (
    _row_to_decision,
    insert_decision_row,
    set_decision_pinned,
)
from agent_memory_lite.utils.time import iso_now


def test_decision_pinned_roundtrips(applied_conn: sqlite3.Connection) -> None:
    workspace = "pin-row-ws"
    insert_decision_row(
        applied_conn,
        decision_id="dec_pin_a",
        workspace_id=workspace,
        title="Pinned decision",
        decision_text="Body.",
        rationale=None,
        supersedes_decision_id=None,
        source_episode_id=None,
        confidence=0.9,
        importance=0.9,
        valid_from=iso_now(),
        created_at=iso_now(),
    )
    set_decision_pinned(
        applied_conn,
        decision_id="dec_pin_a",
        workspace_id=workspace,
        pinned=True,
        updated_at=iso_now(),
    )
    applied_conn.commit()
    row = applied_conn.execute("SELECT * FROM decisions WHERE id = ?", ("dec_pin_a",)).fetchone()
    decision = _row_to_decision(row)
    assert decision.pinned is True


def test_core_memory_pinned_roundtrips(applied_conn: sqlite3.Connection) -> None:
    workspace = "pin-row-core-ws"
    upsert_core_memory_row(
        applied_conn,
        core_id="core_pin_a",
        workspace_id=workspace,
        key="local_only",
        value="Never call cloud LLMs.",
        source_episode_id=None,
        confidence=0.99,
        importance=0.99,
        timestamp=iso_now(),
    )
    applied_conn.execute("UPDATE core_memory SET pinned = 1 WHERE id = ?", ("core_pin_a",))
    applied_conn.commit()
    row = applied_conn.execute("SELECT * FROM core_memory WHERE id = ?", ("core_pin_a",)).fetchone()
    core = _row_to_core(row)
    assert core.pinned is True
