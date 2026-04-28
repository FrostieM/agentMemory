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
from agent_memory_lite.vector_store.base import VectorHit, VectorRow


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


class FakeVectorStore:
    """In-memory vector store. Cosine similarity, namespace-keyed dicts."""

    backend = "fake"

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, VectorRow]] = {}

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def upsert(self, namespace: str, rows: list[VectorRow]) -> None:
        bucket = self._tables.setdefault(namespace, {})
        for row in rows:
            bucket[row.id] = row

    def query(
        self,
        namespace: str,
        vector: np.ndarray,
        *,
        workspace_id: str,
        k: int = 10,
    ) -> list[VectorHit]:
        bucket = self._tables.get(namespace, {})
        norm_query = float(np.linalg.norm(vector)) or 1.0
        scored: list[VectorHit] = []
        for row in bucket.values():
            if row.workspace_id != workspace_id:
                continue
            row_norm = float(np.linalg.norm(row.vector)) or 1.0
            similarity = float(np.dot(vector, row.vector)) / (norm_query * row_norm)
            scored.append(
                VectorHit(
                    id=row.id,
                    workspace_id=row.workspace_id,
                    score=similarity,
                    metadata=dict(row.metadata),
                )
            )
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:k]

    def delete(self, namespace: str, ids: list[str]) -> int:
        bucket = self._tables.get(namespace, {})
        removed = 0
        for row_id in ids:
            if row_id in bucket:
                del bucket[row_id]
                removed += 1
        return removed

    def drop_namespace(self, namespace: str) -> None:
        self._tables.pop(namespace, None)

    def count(self, namespace: str, *, workspace_id: str | None = None) -> int:
        bucket = self._tables.get(namespace, {})
        if workspace_id is None:
            return len(bucket)
        return sum(1 for row in bucket.values() if row.workspace_id == workspace_id)

    def list_ids(self, namespace: str, *, workspace_id: str | None = None) -> list[str]:
        bucket = self._tables.get(namespace, {})
        return [
            row.id
            for row in bucket.values()
            if workspace_id is None or row.workspace_id == workspace_id
        ]


@pytest.fixture
def fake_embedding_provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
def fake_vector_store() -> FakeVectorStore:
    return FakeVectorStore()


@pytest.fixture
def app_factory(
    settings_factory,
    fake_embedding_provider: FakeEmbeddingProvider,
    fake_vector_store: FakeVectorStore,
):
    """Build a FastAPI app with embedding + vector deps overridden by fakes."""

    from agent_memory_lite.api import deps  # noqa: PLC0415
    from agent_memory_lite.api.app import create_app  # noqa: PLC0415

    def _build(**settings_overrides: object):
        settings = settings_factory(**settings_overrides)
        deps.reset_dependency_singletons()
        app = create_app(settings)
        app.dependency_overrides[deps.get_embedding_provider_dep] = lambda: fake_embedding_provider
        app.dependency_overrides[deps.get_vector_store_dep] = lambda: fake_vector_store
        return app

    yield _build
    deps.reset_dependency_singletons()


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
            "MEMORY_FORBID_DEFAULT_WORKSPACE": "false",
            "MEMORY_ENFORCE_WORKSPACE_MANIFEST": "true",
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
        return Settings(_env_file=None)  # type: ignore[call-arg]

    return _factory
