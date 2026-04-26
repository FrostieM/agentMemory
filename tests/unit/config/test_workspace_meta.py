from __future__ import annotations

import sqlite3

from agent_memory_lite.config.workspace_meta import all_for, get, set_value


def test_get_returns_none_for_missing(applied_conn: sqlite3.Connection) -> None:
    assert get(applied_conn, "default", "embedding_model") is None


def test_set_and_get_roundtrip(applied_conn: sqlite3.Connection) -> None:
    set_value(applied_conn, "default", "embedding_model", "intfloat/multilingual-e5-small")
    assert get(applied_conn, "default", "embedding_model") == "intfloat/multilingual-e5-small"


def test_set_value_upserts(applied_conn: sqlite3.Connection) -> None:
    set_value(applied_conn, "default", "embedding_dim", "384")
    set_value(applied_conn, "default", "embedding_dim", "768")
    assert get(applied_conn, "default", "embedding_dim") == "768"


def test_all_for_returns_workspace_subset(applied_conn: sqlite3.Connection) -> None:
    set_value(applied_conn, "default", "a", "1")
    set_value(applied_conn, "default", "b", "2")
    set_value(applied_conn, "other", "c", "3")
    assert all_for(applied_conn, "default") == {"a": "1", "b": "2"}
    assert all_for(applied_conn, "other") == {"c": "3"}
