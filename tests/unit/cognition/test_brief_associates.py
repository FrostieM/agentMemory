"""Phase 2: brief surfaces Hebbian associates of active decisions."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.repositories.soft_edges_repo import upsert_soft_edge
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
OUTCOME_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)
HEBBIAN_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0003_hebbian.sql"


@pytest.fixture(autouse=True)
def _isolate_brief_cache() -> Iterator[None]:
    from agent_memory_lite.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    yield
    brief_mod._BRIEF_CACHE.clear()


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    c.executescript(HEBBIAN_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_decision(conn: sqlite3.Connection, *, id_: str, outcome: float = 0.5) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, 'ws', 't', 'b', ?, 'active', ?, ?, ?, ?, 0)""",
        (id_, f"gist for {id_}", iso_now(), iso_now(), iso_now(), outcome),
    )
    conn.commit()


def _seed_theory(conn: sqlite3.Connection, *, id_: str, outcome: float = 0.5) -> None:
    conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, claim, gist, status, created_at, updated_at,
            outcome_score)
           VALUES (?, 'ws', 't', 'c', ?, 'active', ?, ?, ?)""",
        (id_, f"gist for {id_}", iso_now(), iso_now(), outcome),
    )
    conn.commit()


def test_associates_section_appears_when_edges_exist(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_seed", outcome=0.6)
    _seed_theory(conn, id_="th_assoc", outcome=0.3)
    upsert_soft_edge(
        conn,
        workspace_id="ws",
        src="decision:dec_seed",
        dst="theory:th_assoc",
        kind="co_retrieved",
        weight_increment=2.0,  # well above min_edge_weight=0.1
    )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" in brief.body_md
    assert "theory:th_assoc" in brief.body_md


def test_associates_section_absent_with_no_edges(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_alone", outcome=0.6)
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" not in brief.body_md


def test_associates_section_absent_when_no_decisions(conn: sqlite3.Connection) -> None:
    """No seeds → no associates section."""
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" not in brief.body_md
