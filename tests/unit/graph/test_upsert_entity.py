from __future__ import annotations

import sqlite3

from agent_memory_lite.graph.upsert_entity import upsert_entity
from agent_memory_lite.models.entities import EntityIn


def _payload(**overrides: object) -> EntityIn:
    payload: dict[str, object] = {
        "workspace_id": "default",
        "type": "tool",
        "canonical_name": "SQLite",
    }
    payload.update(overrides)
    return EntityIn(**payload)


def test_first_call_creates_entity(applied_conn: sqlite3.Connection) -> None:
    entity = upsert_entity(applied_conn, _payload())
    assert entity.id.startswith("ent_")
    assert entity.canonical_name == "sqlite"


def test_second_call_returns_same_id(applied_conn: sqlite3.Connection) -> None:
    a = upsert_entity(applied_conn, _payload())
    b = upsert_entity(applied_conn, _payload(canonical_name="sqlite"))
    assert a.id == b.id


def test_aliases_merged(applied_conn: sqlite3.Connection) -> None:
    upsert_entity(applied_conn, _payload(aliases=["sqlite3"]))
    merged = upsert_entity(applied_conn, _payload(aliases=["sqlite-tool"]))
    assert "sqlite3" in merged.aliases
    assert "sqlite-tool" in merged.aliases


def test_workspace_isolation(applied_conn: sqlite3.Connection) -> None:
    a = upsert_entity(applied_conn, _payload(workspace_id="default"))
    b = upsert_entity(applied_conn, _payload(workspace_id="other"))
    assert a.id != b.id
