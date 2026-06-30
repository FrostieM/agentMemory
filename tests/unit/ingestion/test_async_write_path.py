"""Batch E: async persist-then-enqueue write path -- gate tests.

The gate: with MEMORY_DEFER_EMBEDDING + MEMORY_DEFER_EXTRACTION on, ingest_episode
persists the episode and returns WITHOUT issuing ANY embedding or extractor call
on the request thread (p50 latency drops to a pure SQLite write); the brain-pass
extraction-drain step then runs the extractor off-thread, writes the candidates,
and clears extraction_pending. Deferred embeddings keep their existing
embedding_id-IS-NULL + vector-repair healing.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from agent_memory_lite.embeddings.base import EmbeddingKind
from agent_memory_lite.ingestion import auto_promote as auto_promote_mod
from agent_memory_lite.ingestion import episode_pipeline
from agent_memory_lite.ingestion import extraction_repair as extraction_repair_mod
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.extraction_repair import drain_pending_extraction
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn


class _SpyProvider:
    """Embedding provider that mirrors SentenceTransformersProvider's LAZY load:
    the (simulated) model loads on first ``.dim`` OR ``.embed_batch``. Counts both
    ``load_calls`` (the heavy op pin_or_check would trigger via .dim) and
    ``embed_calls`` -- so the async-write gate can assert the request thread does
    NEITHER, not just zero embeds."""

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim
        self.embed_calls = 0
        self.load_calls = 0
        self._loaded = False

    def _load(self) -> None:
        if not self._loaded:
            self.load_calls += 1
            self._loaded = True

    @property
    def name(self) -> str:
        return "spy:test-64"

    @property
    def dim(self) -> int:
        self._load()  # real ST provider loads the model on first .dim access
        return self._dim

    def embed_batch(self, texts: list[str], *, kind: EmbeddingKind = "doc") -> np.ndarray:
        del kind
        self._load()
        self.embed_calls += 1
        return np.zeros((len(texts), self._dim), dtype=np.float32)


def _episode(text: str, **overrides: object) -> EpisodeIn:
    payload: dict[str, object] = {
        "workspace_id": "default",
        "source_type": EpisodeSource.AGENT_ACTION,
        "raw_text": text,
        "trust_level": TrustLevel.USER_ASSERTED,
        "importance": 0.7,
    }
    payload.update(overrides)
    return EpisodeIn(**payload)


def _pending(conn: sqlite3.Connection, episode_id: str) -> int:
    row = conn.execute(
        "SELECT extraction_pending FROM episodes WHERE id = ?", (episode_id,)
    ).fetchone()
    return int(row["extraction_pending"])


def _embedding_id(conn: sqlite3.Connection, chunk_id: str) -> str | None:
    row = conn.execute("SELECT embedding_id FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    return row["embedding_id"]


def _candidate_count(conn: sqlite3.Connection, workspace_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()[0]
    )


# ============================================================
# the core gate: 0 embed/extract calls on the request thread
# ============================================================


def test_async_ingest_issues_no_embed_or_extract_on_request_thread(
    applied_conn: sqlite3.Connection,
    fake_vector_store,
    settings_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_settings = settings_factory(
        MEMORY_DEFER_EMBEDDING="true",
        MEMORY_DEFER_EXTRACTION="true",
        OLLAMA_PROBE_SKIP="true",
    )
    monkeypatch.setattr(episode_pipeline, "get_settings", lambda: async_settings)
    spy = _SpyProvider()

    result = ingest_episode(
        applied_conn,
        _episode("Decision: adopt the async write path for episodes."),
        embedding_provider=spy,
        vector_store=fake_vector_store,
        auto_promote_settings=async_settings,
    )

    # 0 embeddings AND 0 model loads on the request thread: the deferred write is
    # a pure SQLite write. load_calls guards the pin_or_check(provider.dim) path,
    # which lazily loads the embedding model -- the heaviest request-thread op,
    # which embed-call counting alone would miss.
    assert spy.embed_calls == 0
    assert spy.load_calls == 0
    # Nothing slow ran inline: not embedded, no candidates written synchronously.
    assert result.embedded is False
    assert result.candidates_written == 0
    # The episode is durably persisted + flagged for deferred extraction, and its
    # chunk has no embedding yet (both heal on the brain pass).
    assert _pending(applied_conn, result.episode.id) == 1
    assert _embedding_id(applied_conn, result.chunk.id) is None


# ============================================================
# the worker drain: populates candidates + clears pending
# ============================================================


def test_brain_pass_drain_extracts_and_clears_pending(
    applied_conn: sqlite3.Connection,
    fake_vector_store,
    settings_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_settings = settings_factory(
        MEMORY_DEFER_EMBEDDING="true",
        MEMORY_DEFER_EXTRACTION="true",
        OLLAMA_PROBE_SKIP="true",
    )
    monkeypatch.setattr(episode_pipeline, "get_settings", lambda: async_settings)
    result = ingest_episode(
        applied_conn,
        _episode("Decision: always redact secrets before persisting an episode."),
        embedding_provider=_SpyProvider(),
        vector_store=fake_vector_store,
        auto_promote_settings=async_settings,
    )
    assert _pending(applied_conn, result.episode.id) == 1
    before = _candidate_count(applied_conn, "default")

    drained = drain_pending_extraction(
        applied_conn, workspace_id="default", settings=async_settings, limit=64
    )

    assert drained.episodes_drained == 1
    assert drained.candidates_written >= 1  # the heuristic extractor wrote a candidate
    assert _candidate_count(applied_conn, "default") == before + drained.candidates_written
    # The flag is cleared so the next pass does not re-extract it.
    assert _pending(applied_conn, result.episode.id) == 0


def test_drain_is_failure_soft_and_leaves_episode_pending(
    applied_conn: sqlite3.Connection,
    fake_vector_store,
    settings_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_settings = settings_factory(
        MEMORY_DEFER_EMBEDDING="true",
        MEMORY_DEFER_EXTRACTION="true",
        OLLAMA_PROBE_SKIP="true",
    )
    monkeypatch.setattr(episode_pipeline, "get_settings", lambda: async_settings)
    result = ingest_episode(
        applied_conn,
        _episode("Decision: ship it."),
        embedding_provider=_SpyProvider(),
        vector_store=fake_vector_store,
        auto_promote_settings=async_settings,
    )

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("extractor exploded")

    monkeypatch.setattr(auto_promote_mod, "auto_promote", _boom)
    drained = drain_pending_extraction(
        applied_conn, workspace_id="default", settings=async_settings, limit=64
    )

    # A failed extraction does not clear the flag (retried next pass) and the
    # drain does not raise.
    assert drained.episodes_drained == 0
    assert _pending(applied_conn, result.episode.id) == 1


def test_drain_is_atomic_failed_clear_does_not_duplicate_candidates(
    applied_conn: sqlite3.Connection,
    fake_vector_store,
    settings_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idempotency on retry: if the pending-clear fails AFTER candidates are
    extracted (a crash between the candidate writes and the clear), the atomic
    per-episode transaction rolls the candidates back too, leaving
    extraction_pending = 1. A clean re-drain then writes the candidates EXACTLY
    once -- no duplicates (the property auto_promote alone does NOT provide,
    since each write_memory_candidate commits a fresh-id row)."""
    async_settings = settings_factory(
        MEMORY_DEFER_EMBEDDING="true",
        MEMORY_DEFER_EXTRACTION="true",
        OLLAMA_PROBE_SKIP="true",
    )
    monkeypatch.setattr(episode_pipeline, "get_settings", lambda: async_settings)
    result = ingest_episode(
        applied_conn,
        _episode("Decision: enforce idempotent deferred extraction."),
        embedding_provider=_SpyProvider(),
        vector_store=fake_vector_store,
        auto_promote_settings=async_settings,
    )
    before = _candidate_count(applied_conn, "default")

    # First drain: the clear raises -> the whole episode's transaction rolls back.
    def _boom_clear(*_a: object, **_k: object) -> None:
        raise RuntimeError("clear crashed after candidate writes")

    monkeypatch.setattr(extraction_repair_mod, "clear_episode_extraction_pending", _boom_clear)
    failed = drain_pending_extraction(
        applied_conn, workspace_id="default", settings=async_settings, limit=64
    )
    assert failed.episodes_drained == 0
    assert _candidate_count(applied_conn, "default") == before  # candidates rolled back
    assert _pending(applied_conn, result.episode.id) == 1  # still pending -> will retry

    # Clean re-drain: restore the real clear; candidates land EXACTLY once.
    monkeypatch.undo()
    ok = drain_pending_extraction(
        applied_conn, workspace_id="default", settings=async_settings, limit=64
    )
    assert ok.episodes_drained == 1
    assert ok.candidates_written >= 1
    assert _candidate_count(applied_conn, "default") == before + ok.candidates_written
    assert _pending(applied_conn, result.episode.id) == 0


# ============================================================
# the default path is unchanged: inline extraction, no pending flag
# ============================================================


def test_default_path_extracts_inline_and_leaves_no_pending(
    applied_conn: sqlite3.Connection,
    fake_vector_store,
    settings_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_settings = settings_factory(OLLAMA_PROBE_SKIP="true")  # defers OFF (default)
    monkeypatch.setattr(episode_pipeline, "get_settings", lambda: sync_settings)
    result = ingest_episode(
        applied_conn,
        _episode("Decision: keep the synchronous write path as the default."),
        embedding_provider=_SpyProvider(),
        vector_store=fake_vector_store,
        auto_promote_settings=sync_settings,
    )

    # Inline extraction ran (candidate written synchronously) and the episode is
    # NOT flagged pending -- nothing for the drain to do.
    assert result.candidates_written >= 1
    assert _pending(applied_conn, result.episode.id) == 0
