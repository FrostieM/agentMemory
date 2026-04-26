"""Factory that picks an `EmbeddingProvider` from settings.

The default is sentence-transformers; users can opt into Ollama via env. Tests
inject their own provider by overriding the FastAPI dependency.
"""

from __future__ import annotations

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.embeddings.base import (
    EmbeddingProvider,
    EmbeddingProviderUnavailableError,
)
from agent_memory_lite.embeddings.ollama_provider import OllamaProvider
from agent_memory_lite.embeddings.sentence_transformers_provider import (
    SentenceTransformersProvider,
)


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
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
