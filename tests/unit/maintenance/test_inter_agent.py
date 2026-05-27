"""v3.1 Vector 6 — inter-agent negotiation MVP."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.maintenance import inter_agent as ia
from agent_memory_lite.maintenance.inter_agent import InterAgentBelief


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_INTER_AGENT_MIN_TITLE_LEN", raising=False)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Fresh schema from the root migration runner."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(tmp_path / "ia.db")
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_episode(conn: sqlite3.Connection, ep_id: str, ws: str = "ws") -> None:
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, ?, 'agent_action', 'fixture', '2026-05-19T00:00:00+00:00')",
        (ep_id, ws),
    )
    conn.commit()


def _seed_decision(
    conn: sqlite3.Connection, did: str, title: str, text: str, ws: str = "ws"
) -> None:
    conn.execute(
        """INSERT INTO decisions (
            id, workspace_id, title, decision_text, rationale,
            status, valid_from, created_at, updated_at
        ) VALUES (?, ?, ?, ?, '', 'active', ?, ?, ?)""",
        (
            did,
            ws,
            title,
            text,
            "2026-05-19T00:00:00+00:00",
            "2026-05-19T00:00:00+00:00",
            "2026-05-19T00:00:00+00:00",
        ),
    )
    conn.commit()


def test_export_empty_on_fresh_workspace(conn: sqlite3.Connection) -> None:
    """Fresh workspace with no decisions → empty list.

    Updated 2026-05-20: the master ``is_enabled()`` gate was removed
    because inter-agent functions are only invoked by an explicit
    operator/route — the toggle on a function-call-only feature was
    dead weight. Operator gates by choosing whether to call.
    """
    assert ia.export_workspace(conn, workspace_id="ws") == []


def test_export_returns_active_decisions(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_decision(conn, "dec_a", "Use kelly sizing", "quarter kelly")
    out = ia.export_workspace(conn, workspace_id="ws")
    assert len(out) == 1
    assert out[0].foreign_id == "dec_a"
    assert out[0].title == "Use kelly sizing"
    assert out[0].decision_text == "quarter kelly"


def test_import_skips_when_no_conflict(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same title + identical body → no conflict, no candidate."""
    _seed_episode(conn, "ep_neg")
    _seed_decision(conn, "dec_local", "Use kelly sizing", "quarter kelly")
    beliefs = [
        InterAgentBelief(
            foreign_id="dec_foreign",
            title="Use kelly sizing",
            decision_text="quarter kelly",  # identical
            confidence=0.5,
            outcome_score=0.0,
            foreign_workspace_id="other_ws",
        )
    ]
    n = ia.import_beliefs(conn, workspace_id="ws", beliefs=beliefs, source_episode_id="ep_neg")
    assert n == 0


def test_import_emits_candidate_on_conflict(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same title + different body → 1 candidate landed."""
    _seed_episode(conn, "ep_conflict")
    _seed_decision(conn, "dec_local", "Use kelly sizing", "quarter kelly")
    beliefs = [
        InterAgentBelief(
            foreign_id="dec_foreign",
            title="Use kelly sizing",
            decision_text="full kelly",  # differs
            confidence=0.6,
            outcome_score=0.0,
            foreign_workspace_id="other_ws",
        )
    ]
    n = ia.import_beliefs(conn, workspace_id="ws", beliefs=beliefs, source_episode_id="ep_conflict")
    assert n == 1
    row = conn.execute(
        "SELECT kind, subject, predicate, object, status FROM candidates "
        "WHERE id = 'cand_neg_dec_foreign__dec_local'"
    ).fetchone()
    assert row is not None
    assert row["kind"] == "negotiation"
    assert row["predicate"] == "disagrees_with"
    assert row["status"] == "new"


def test_import_idempotent(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running the same import is a no-op (updates in place)."""
    _seed_episode(conn, "ep_idem")
    _seed_decision(conn, "dec_local", "Use kelly sizing", "quarter kelly")
    beliefs = [
        InterAgentBelief(
            foreign_id="dec_foreign",
            title="Use kelly sizing",
            decision_text="full kelly",
            confidence=0.6,
            outcome_score=0.0,
            foreign_workspace_id="other_ws",
        )
    ]
    assert (
        ia.import_beliefs(conn, workspace_id="ws", beliefs=beliefs, source_episode_id="ep_idem")
        == 1
    )
    # Second pass: cursor.rowcount == 0 → "" → not counted.
    assert (
        ia.import_beliefs(conn, workspace_id="ws", beliefs=beliefs, source_episode_id="ep_idem")
        == 1
    )
    n_rows = conn.execute("SELECT COUNT(*) FROM candidates WHERE kind = 'negotiation'").fetchone()[
        0
    ]
    assert n_rows == 1


def test_import_skips_short_titles(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Titles shorter than min_title_len are ignored."""
    monkeypatch.setenv("MEMORY_INTER_AGENT_MIN_TITLE_LEN", "20")
    _seed_episode(conn, "ep_short")
    _seed_decision(conn, "dec_local", "short", "body one")
    beliefs = [
        InterAgentBelief(
            foreign_id="dec_foreign",
            title="short",
            decision_text="body two",
            confidence=0.5,
            outcome_score=0.0,
            foreign_workspace_id="other_ws",
        )
    ]
    assert (
        ia.import_beliefs(conn, workspace_id="ws", beliefs=beliefs, source_episode_id="ep_short")
        == 0
    )


def test_import_returns_zero_when_no_episode(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty source_episode_id → no writes (FK protection)."""
    _seed_decision(conn, "dec_local", "Use kelly sizing", "quarter kelly")
    beliefs = [
        InterAgentBelief(
            foreign_id="dec_foreign",
            title="Use kelly sizing",
            decision_text="full kelly",
            confidence=0.5,
            outcome_score=0.0,
            foreign_workspace_id="other_ws",
        )
    ]
    assert ia.import_beliefs(conn, workspace_id="ws", beliefs=beliefs, source_episode_id="") == 0


def test_belief_to_dict_roundtrip() -> None:
    b = InterAgentBelief(
        foreign_id="dec_x",
        title="t",
        decision_text="body",
        confidence=0.5,
        outcome_score=-0.1,
        foreign_workspace_id="ws_other",
    )
    d = b.to_dict()
    assert d["foreign_id"] == "dec_x"
    assert d["confidence"] == 0.5
    assert d["foreign_workspace_id"] == "ws_other"
