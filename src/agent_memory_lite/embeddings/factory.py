"""Factory that picks an `EmbeddingProvider` from settings.

The default is sentence-transformers; users can opt into Ollama via env. Tests
inject their own provider by overriding the FastAPI dependency.

The chosen provider is cached process-wide, keyed by the embedding settings that
determine its identity (backend, model, batch size, base URL). The
sentence-transformers backend lazily loads a torch model on its first embed call
(~10 s cold); before this cache every caller that built a fresh provider -- the
brain_pass background thread, causal-embedding, the MCP stdio runtime -- paid
that cold load again, so a single tick could burn most of its wall-clock on
redundant torch loads. One warm instance is shared instead. The provider's
encode path is read-only and its lazy load is internally locked, so a single
instance is safe to share across the HTTP, MCP, and maintenance threads.
"""

from __future__ import annotations

import threading

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.embeddings.base import (
    EmbeddingProvider,
    EmbeddingProviderUnavailableError,
)
from agent_memory_lite.embeddings.ollama_provider import OllamaProvider
from agent_memory_lite.embeddings.sentence_transformers_provider import (
    SentenceTransformersProvider,
)

# Provider identity = the settings fields that change which model/endpoint a
# provider talks to. Anything outside this tuple does not change the embedding
# vectors, so a cache hit on the same tuple is always correct.
_CacheKey = tuple[str, str, int, str]
_provider_cache: dict[_CacheKey, EmbeddingProvider] = {}
_provider_cache_lock = threading.Lock()


def _cache_key(settings: Settings) -> _CacheKey:
    return (
        settings.embedding_backend,
        settings.embedding_model,
        int(settings.embedding_batch_size),
        settings.embedding_base_url or "",
    )


def _build_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_backend == "ollama":
        if not settings.embedding_base_url:
            raise EmbeddingProviderUnavailableError(
                "EMBEDDING_BACKEND=ollama requires EMBEDDING_BASE_URL"
            )
        return OllamaProvider(
            base_url=settings.embedding_base_url,
            model_name=settings.embedding_model,
            batch_size=settings.embedding_batch_size,
        )
    return SentenceTransformersProvider(
        settings.embedding_model,
        batch_size=settings.embedding_batch_size,
    )


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Return a process-shared provider for these embedding settings.

    Cached per (backend, model, batch_size, base_url) so the expensive
    sentence-transformers torch load happens once per process and is reused by
    every caller. The brain_pass thread and causal-embedding previously rebuilt a
    cold provider per call and paid the ~10 s first-embed load each time. A failed
    build is not cached, so a misconfigured backend keeps raising rather than
    poisoning the cache with ``None``.
    """
    key = _cache_key(settings)
    cached = _provider_cache.get(key)
    if cached is not None:
        return cached
    with _provider_cache_lock:
        # Double-checked: another thread may have built it while we waited.
        cached = _provider_cache.get(key)
        if cached is None:
            cached = _build_provider(settings)
            _provider_cache[key] = cached
        return cached


def reset_embedding_provider_cache() -> None:
    """Drop cached providers so the next call rebuilds (tests / settings reload)."""
    with _provider_cache_lock:
        _provider_cache.clear()
