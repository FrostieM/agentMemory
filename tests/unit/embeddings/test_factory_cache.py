"""The embedding factory caches one warm provider per settings identity.

Before the cache, every caller that built a fresh provider (the brain_pass
background thread, causal-embedding, the MCP stdio runtime) paid the full
sentence-transformers torch cold-load again -- ~10 s of redundant work per
embedding step of a maintenance tick. These tests lock the cache contract:
same settings -> same instance, distinct settings -> distinct instance, and a
reset rebuilds. They never call ``embed_batch`` so no model is downloaded
(construction is lazy).
"""

from __future__ import annotations

import pytest

from agent_memory_lite.embeddings.base import EmbeddingProviderUnavailableError
from agent_memory_lite.embeddings.factory import (
    get_embedding_provider,
    reset_embedding_provider_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    reset_embedding_provider_cache()
    yield
    reset_embedding_provider_cache()


def test_same_settings_returns_same_instance(settings_factory) -> None:
    settings = settings_factory()
    first = get_embedding_provider(settings)
    second = get_embedding_provider(settings)
    assert first is second  # warm provider reused, no second cold load


def test_distinct_model_returns_distinct_instance(settings_factory) -> None:
    a = get_embedding_provider(settings_factory(EMBEDDING_MODEL="intfloat/multilingual-e5-small"))
    b = get_embedding_provider(settings_factory(EMBEDDING_MODEL="intfloat/multilingual-e5-base"))
    assert a is not b  # cache key includes the model name
    assert a.name != b.name


def test_distinct_settings_objects_same_identity_share_instance(settings_factory) -> None:
    """Two separate Settings with the same embedding identity (the brain_pass /
    causal-embedding case, which each call ``get_settings()`` freshly) must still
    hit the same warm provider -- the key is the field tuple, not object id."""
    first = get_embedding_provider(settings_factory())
    second = get_embedding_provider(settings_factory())
    assert isinstance(first, type(second))
    assert first is second


def test_reset_rebuilds(settings_factory) -> None:
    settings = settings_factory()
    first = get_embedding_provider(settings)
    reset_embedding_provider_cache()
    second = get_embedding_provider(settings)
    assert first is not second  # reset dropped the cached instance


def test_ollama_misconfig_is_not_cached(settings_factory) -> None:
    """A backend that fails to build must keep raising, not poison the cache with
    a half-built entry that a later (now-valid) call would wrongly reuse."""
    bad = settings_factory(EMBEDDING_BACKEND="ollama", EMBEDDING_BASE_URL="")
    with pytest.raises(EmbeddingProviderUnavailableError):
        get_embedding_provider(bad)
    # Second call still raises (nothing cached for that key).
    with pytest.raises(EmbeddingProviderUnavailableError):
        get_embedding_provider(bad)
