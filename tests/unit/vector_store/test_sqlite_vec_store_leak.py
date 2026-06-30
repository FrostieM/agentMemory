"""M9 (global-audit 2026-06-30): SqliteVecStore.open must not leak the freshly
opened connection when the sqlite-vec extension fails to load."""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path

import pytest

from agent_memory_lite.vector_store.base import VectorStoreUnavailableError
from agent_memory_lite.vector_store.sqlite_vec_store import SqliteVecStore


class _TrackingConn:
    """Wraps a real in-memory connection and counts close() calls."""

    def __init__(self, inner: sqlite3.Connection, closed: list[int]) -> None:
        self._inner = inner
        self._closed = closed

    def enable_load_extension(self, _flag: bool) -> None:  # no-op for the fake path
        return None

    def close(self) -> None:
        self._closed.append(1)
        self._inner.close()

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def test_open_closes_connection_when_extension_load_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fake sqlite_vec whose load() raises, simulating an unloadable extension.
    fake = types.ModuleType("sqlite_vec")

    def _boom(_conn: object) -> None:
        raise sqlite3.OperationalError("cannot load extension")

    fake.load = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sqlite_vec", fake)

    closed: list[int] = []
    real_connect = sqlite3.connect

    def _fake_connect(_path: object, *a: object, **k: object) -> _TrackingConn:
        return _TrackingConn(real_connect(":memory:"), closed)

    monkeypatch.setattr(sqlite3, "connect", _fake_connect)

    store = SqliteVecStore(tmp_path / "vec.db")
    with pytest.raises(VectorStoreUnavailableError):
        store.open()

    # The connection opened inside open() was closed instead of being orphaned.
    assert closed == [1]
