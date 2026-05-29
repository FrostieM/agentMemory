"""SentenceTransformersProvider._load thread-safety (release plan step 10.3).

The provider is a per-process singleton shared by the MCP handler threads
(``asyncio.to_thread``) and the startup warm-up thread. Without a lock the
lazy ``_load`` had a check-then-set race that let two threads both build the
model. This pins the double-checked-locking fix without loading a real model.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from agent_memory_lite.embeddings.sentence_transformers_provider import (
    SentenceTransformersProvider,
)


class _FakeModel:
    def get_sentence_embedding_dimension(self) -> int:
        return 8


def test_load_builds_model_once_under_concurrency(monkeypatch) -> None:
    provider = SentenceTransformersProvider("fake/model")
    builds: list[int] = []

    def _build() -> Any:
        builds.append(1)
        time.sleep(0.02)  # widen the race window so a missing lock double-builds
        return _FakeModel()

    monkeypatch.setattr(provider, "_build_model", _build)

    threads = [threading.Thread(target=provider._load) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(builds) == 1  # exactly one build despite 8 concurrent first-callers
    assert provider._dim == 8
    assert provider._model is not None


def test_load_is_idempotent_after_first(monkeypatch) -> None:
    provider = SentenceTransformersProvider("fake/model")
    builds: list[int] = []

    def _build() -> Any:
        builds.append(1)
        return _FakeModel()

    monkeypatch.setattr(provider, "_build_model", _build)
    provider._load()
    provider._load()
    provider._load()
    assert len(builds) == 1  # subsequent loads reuse the cached model
