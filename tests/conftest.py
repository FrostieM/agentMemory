"""Shared pytest fixtures.

The fixtures intentionally avoid real network and ML-model downloads. Test files
that need a real provider should be marked `needs_st_model` or `needs_ollama`
and skipped in default runs.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from agent_memory_lite.config.settings import Settings, reset_settings_cache
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.embeddings.base import EmbeddingKind


class FakeEmbeddingProvider:
    """Deterministic provider that hashes inputs to a fixed-dim vector.

    Stable across runs and process restarts; same input always produces the same
    vector. The hash space is tiny enough to keep tests fast (no model download).
    """

    def __init__(self, dim: int = 64, name: str = "fake:test-64") -> None:
        self._dim = dim
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def dim(self) -> int:
        return self._dim

    def embed_batch(self, texts: list[str], *, kind: EmbeddingKind = "doc") -> np.ndarray:
        del kind
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        rows: list[np.ndarray] = []
        for text in texts:
            buf = b""
            seed = text.encode("utf-8")
            counter = 0
            while len(buf) < self._dim * 2:
                buf += hashlib.blake2b(seed + counter.to_bytes(2, "big"), digest_size=64).digest()
                counter += 1
            ints = np.frombuffer(buf[: self._dim * 2], dtype=np.uint16).astype(np.float32)
            ints = (ints / np.float32(65535.0)) * 2.0 - 1.0
            norm = float(np.linalg.norm(ints))
            if norm > 0:
                ints = ints / norm
            rows.append(ints.astype(np.float32))
        return np.vstack(rows)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "memory.db"


@pytest.fixture
def tmp_vector_path(tmp_path: Path) -> Path:
    return tmp_path / "vectors.lance"


@pytest.fixture
def fresh_conn(tmp_db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_connection(tmp_db_path)
    try:
        yield conn
    finally:
        close_connection(conn)


@pytest.fixture
def applied_conn(fresh_conn: sqlite3.Connection) -> sqlite3.Connection:
    """A connection with all migrations applied against the project migrations dir."""
    apply_migrations(fresh_conn, MIGRATION_DIR)
    return fresh_conn


@pytest.fixture
def fake_embedding_provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
def settings_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_db_path: Path,
    tmp_vector_path: Path,
):
    """Build a Settings instance with overrides applied through the env."""

    def _factory(**overrides: object) -> Settings:
        defaults: dict[str, str] = {
            "LOCAL_ONLY": "true",
            "ALLOW_REMOTE_PROVIDERS": "false",
            "MEMORY_API_PORT": "8765",
            "MEMORY_DB_PATH": str(tmp_db_path),
            "VECTOR_DB_PATH": str(tmp_vector_path),
            "MEMORY_WORKSPACE_ID": "default",
            "EMBEDDING_BACKEND": "sentence_transformers",
            "EMBEDDING_MODEL": "intfloat/multilingual-e5-small",
            "VECTOR_BACKEND": "lancedb",
            "LLM_BACKEND": "ollama",
            "LLM_BASE_URL": "http://127.0.0.1:11434",
            "LLM_MODEL": "qwen2.5:7b-instruct",
            "OLLAMA_PROBE_SKIP": "true",
            "LOG_LEVEL": "WARNING",
        }
        for env_var in (
            *defaults,
            "EMBEDDING_BASE_URL",
            "POSTHOG_API_KEY",
            "SENTRY_DSN",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ):
            monkeypatch.delenv(env_var, raising=False)
        for env_var, value in defaults.items():
            monkeypatch.setenv(env_var, value)
        for env_var, value in overrides.items():
            monkeypatch.setenv(env_var, str(value))
        reset_settings_cache()
        return Settings()

    return _factory
