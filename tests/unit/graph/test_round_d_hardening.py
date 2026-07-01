"""global-audit round-D: graph-write secret redaction + SQL-safety hardening."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.graph.upsert_entity import upsert_entity
from agent_memory_lite.graph.write_fact import write_fact
from agent_memory_lite.maintenance.row_retention_episode_refs import _episode_referencing_pairs
from agent_memory_lite.models.entities import EntityIn
from agent_memory_lite.models.facts import FactIn

_GHP = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_ANT = "sk-ant-api03-LEAKLEAKLEAKLEAKLEAKLEAK"


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    return c


def test_upsert_entity_redacts_secrets_in_aliases_and_properties(conn: sqlite3.Connection) -> None:
    """round-D: upsert_entity bypassed write_canonical -- a secret in aliases /
    properties was persisted cleartext in entities.aliases_json / properties_json."""
    e = upsert_entity(
        conn,
        EntityIn(
            workspace_id="default",
            type="service",
            canonical_name="api",
            aliases=[f"token {_GHP}"],
            properties={"note": "password=hunter2"},
        ),
    )
    row = conn.execute(
        "SELECT aliases_json, properties_json FROM entities WHERE id = ?", (e.id,)
    ).fetchone()
    blob = f"{row['aliases_json']}{row['properties_json']}"
    assert _GHP not in blob
    assert "hunter2" not in blob
    assert "REDACTED" in blob


def test_write_fact_redacts_secrets_in_fact_text_and_literal(conn: sqlite3.Connection) -> None:
    """round-D: write_fact persisted literal_value / fact_text cleartext in the facts
    table -- the "redaction runs before every v3 write" invariant was violated."""
    e = upsert_entity(conn, EntityIn(workspace_id="default", type="service", canonical_name="svc"))
    result = write_fact(
        conn,
        FactIn(
            workspace_id="default",
            subject_entity_id=e.id,
            relation="uses",
            object_entity_id=None,
            fact_text=f"key {_ANT}",
            literal_value=_GHP,
            source_episode_id="ep_x",
        ),
    )
    row = conn.execute(
        "SELECT fact_text, literal_value FROM facts WHERE id = ?", (result.fact.id,)
    ).fetchone()
    blob = f"{row['fact_text']}{row['literal_value']}"
    assert _ANT not in blob
    assert _GHP not in blob
    assert "REDACTED" in blob


def test_episode_ref_discovery_survives_quoted_table_name(conn: sqlite3.Connection) -> None:
    """round-D: the episode-FK guard interpolated table names into PRAGMA table_info,
    so a table whose name contains a quote produced invalid SQL that was swallowed --
    silently dropping that table from the guard. The bound pragma_table_info() form is
    injection- and quote-safe: discovery still works and does not raise."""
    conn.execute('CREATE TABLE "we\'ird" (id TEXT PRIMARY KEY, episode_id TEXT)')
    # Must not raise, and the normal tables' episode refs are still discovered.
    pairs = _episode_referencing_pairs(conn)
    assert isinstance(pairs, list)
    # the quoted table's episode_id column is now discoverable too (was silently lost).
    assert ("we'ird", "episode_id") in pairs
