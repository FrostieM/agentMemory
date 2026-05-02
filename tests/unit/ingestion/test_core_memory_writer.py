from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.core_memory_writer import write_core_memory
from agent_memory_lite.models.core_memory import CoreMemoryIn
from agent_memory_lite.repositories.core_memory_repo import (
    get_active_by_key,
    list_active_core,
)


def _payload(**overrides: object) -> CoreMemoryIn:
    payload: dict[str, object] = {
        "workspace_id": "default",
        "key": "agent.locality",
        "value": "Agent runs locally.",
        "confidence": 0.95,
        "importance": 0.9,
    }
    payload.update(overrides)
    return CoreMemoryIn(**payload)


def test_first_write_creates_active_row(applied_conn: sqlite3.Connection) -> None:
    item = write_core_memory(applied_conn, _payload())
    assert item.active is True
    assert item.value == "Agent runs locally."


def test_re_write_for_same_key_deactivates_prior(applied_conn: sqlite3.Connection) -> None:
    first = write_core_memory(applied_conn, _payload())
    second = write_core_memory(applied_conn, _payload(value="Updated locality."))
    assert first.id != second.id
    actives = list_active_core(applied_conn, "default")
    assert len(actives) == 1
    assert actives[0].id == second.id


def test_get_active_by_key(applied_conn: sqlite3.Connection) -> None:
    write_core_memory(applied_conn, _payload(value="v1"))
    write_core_memory(applied_conn, _payload(value="v2"))
    item = get_active_by_key(applied_conn, "default", "agent.locality")
    assert item is not None
    assert item.value == "v2"


def test_workspace_isolation(applied_conn: sqlite3.Connection) -> None:
    write_core_memory(applied_conn, _payload(workspace_id="default"))
    write_core_memory(applied_conn, _payload(workspace_id="other", value="other-val"))
    actives_default = list_active_core(applied_conn, "default")
    actives_other = list_active_core(applied_conn, "other")
    assert {a.workspace_id for a in actives_default} == {"default"}
    assert {a.workspace_id for a in actives_other} == {"other"}
