from __future__ import annotations

import sqlite3

import pytest
from tests.conftest import FakeEmbeddingProvider

from agent_memory_lite.config import workspace_meta
from agent_memory_lite.embeddings.dimension_check import (
    EmbeddingDimensionMismatchError,
    pin_or_check,
)


def test_pin_writes_workspace_meta_on_first_call(
    applied_conn: sqlite3.Connection, fake_embedding_provider
) -> None:
    pin_or_check(applied_conn, "default", fake_embedding_provider)
    meta = workspace_meta.all_for(applied_conn, "default")
    assert meta["embedding_model"] == fake_embedding_provider.name
    assert int(meta["embedding_dim"]) == fake_embedding_provider.dim


def test_pin_or_check_is_idempotent(
    applied_conn: sqlite3.Connection, fake_embedding_provider
) -> None:
    pin_or_check(applied_conn, "default", fake_embedding_provider)
    pin_or_check(applied_conn, "default", fake_embedding_provider)
    meta = workspace_meta.all_for(applied_conn, "default")
    assert meta["embedding_model"] == fake_embedding_provider.name


def test_pin_or_check_rejects_dim_drift(applied_conn: sqlite3.Connection) -> None:
    pin_or_check(applied_conn, "default", FakeEmbeddingProvider(dim=64))
    with pytest.raises(EmbeddingDimensionMismatchError, match="dim=64"):
        pin_or_check(applied_conn, "default", FakeEmbeddingProvider(dim=128))


def test_pin_or_check_rejects_model_swap(applied_conn: sqlite3.Connection) -> None:
    pin_or_check(applied_conn, "default", FakeEmbeddingProvider(dim=64, name="fake:a"))
    with pytest.raises(EmbeddingDimensionMismatchError, match="fake:a"):
        pin_or_check(applied_conn, "default", FakeEmbeddingProvider(dim=64, name="fake:b"))
