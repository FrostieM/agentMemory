"""Regression: the sentence-transformers provider loads cache-only.

Root cause of a multi-minute hung ``memory_write``: ``SentenceTransformer``
was constructed WITHOUT ``local_files_only``, so every load pinged
huggingface.co to verify the snapshot revision. That network call has no
app-level timeout, so a slow / blocked HF network stalled the whole
synchronous write path -- and it violated the local-only ("no cloud
calls") contract.

The provider must therefore:
1. attempt a cache-only load first (``local_files_only=True`` -> no network);
2. fall back to a networked fetch ONLY when the model is genuinely absent
   (the one-time bootstrap, like ``ollama pull``).

These tests monkeypatch ``SentenceTransformer`` so they never touch the
network or download a model.
"""

from __future__ import annotations

import sentence_transformers

from agent_memory_lite.embeddings.sentence_transformers_provider import (
    SentenceTransformersProvider,
)


class _FakeModel:
    def get_sentence_embedding_dimension(self) -> int:
        return 384


def test_load_is_cache_only_on_the_warm_path(monkeypatch) -> None:
    """When the model is cached, the provider passes local_files_only=True
    and never makes the networked (no-flag) call."""
    calls: list[dict] = []

    def fake_st(_name: str, **kwargs):
        calls.append(kwargs)
        return _FakeModel()

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", fake_st)

    provider = SentenceTransformersProvider("intfloat/multilingual-e5-small")
    assert provider.dim == 384
    # Exactly one construction, and it was the cache-only one. No second
    # (networked) call -> zero HF Hub traffic on the warm path.
    assert calls == [{"local_files_only": True}]


def test_load_falls_back_to_bootstrap_when_not_cached(monkeypatch) -> None:
    """If the cache-only load raises (model absent), the provider performs
    the one-time networked bootstrap fetch."""
    calls: list[dict] = []

    def fake_st(_name: str, **kwargs):
        calls.append(kwargs)
        if kwargs.get("local_files_only"):
            raise OSError("model not found in local cache")
        return _FakeModel()

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", fake_st)

    provider = SentenceTransformersProvider("intfloat/multilingual-e5-small")
    assert provider.dim == 384
    # First the cache-only attempt, then the networked bootstrap.
    assert calls == [{"local_files_only": True}, {}]
