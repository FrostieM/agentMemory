from __future__ import annotations

import numpy as np

from agent_memory_lite.embeddings.base import EmbeddingProvider


def test_fake_provider_satisfies_protocol(fake_embedding_provider) -> None:
    assert isinstance(fake_embedding_provider, EmbeddingProvider)


def test_fake_provider_returns_normalized_vectors(fake_embedding_provider) -> None:
    vectors = fake_embedding_provider.embed_batch(["hello", "world"])
    assert vectors.shape == (2, fake_embedding_provider.dim)
    norms = np.linalg.norm(vectors, axis=1)
    np.testing.assert_allclose(norms, np.ones(2), atol=1e-5)


def test_fake_provider_is_deterministic(fake_embedding_provider) -> None:
    a = fake_embedding_provider.embed_batch(["hello"])
    b = fake_embedding_provider.embed_batch(["hello"])
    np.testing.assert_array_equal(a, b)


def test_fake_provider_empty_input_returns_empty(fake_embedding_provider) -> None:
    vectors = fake_embedding_provider.embed_batch([])
    assert vectors.shape == (0, fake_embedding_provider.dim)
