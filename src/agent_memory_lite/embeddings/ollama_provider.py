"""Ollama-backed `EmbeddingProvider`.

Calls the local Ollama HTTP API at `<base_url>/api/embed`. The dimension is
discovered on the first successful call and cached. A startup probe (when the
caller wants one) goes against `<base_url>/api/tags`.
"""

from __future__ import annotations

import httpx
import numpy as np

from agent_memory_lite.embeddings.base import (
    EmbeddingKind,
    EmbeddingProvider,
    EmbeddingProviderUnavailableError,
)
from agent_memory_lite.embeddings.batching import iter_batches

DEFAULT_BATCH_SIZE = 32
DEFAULT_TIMEOUT_SECONDS = 30.0


class OllamaProvider(EmbeddingProvider):
    def __init__(
        self,
        base_url: str,
        model_name: str,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._batch_size = batch_size
        self._timeout = timeout
        self._dim: int | None = None

    @property
    def name(self) -> str:
        return f"ollama:{self._model_name}"

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = self._embed_one_for_dim()
        return self._dim

    def probe(self) -> None:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(f"{self._base_url}/api/tags")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingProviderUnavailableError(
                f"ollama probe failed at {self._base_url}: {exc}"
            ) from exc

    def _embed_one_for_dim(self) -> int:
        vectors = self._post_embed(["dimension probe"])
        return int(vectors.shape[1])

    def _post_embed(self, texts: list[str]) -> np.ndarray:
        payload = {"model": self._model_name, "input": texts}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(f"{self._base_url}/api/embed", json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingProviderUnavailableError(
                f"ollama embed call failed at {self._base_url}: {exc}"
            ) from exc
        data = response.json()
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or not embeddings:
            raise EmbeddingProviderUnavailableError(
                f"ollama embed response missing 'embeddings': {data!r}"
            )
        return np.asarray(embeddings, dtype=np.float32)

    def embed_batch(self, texts: list[str], *, kind: EmbeddingKind = "doc") -> np.ndarray:
        del kind  # Ollama embedding models do not need prefixing.
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        chunks: list[np.ndarray] = []
        for batch in iter_batches(texts, self._batch_size):
            chunks.append(self._post_embed(batch))
        result = np.vstack(chunks)
        if self._dim is None:
            self._dim = int(result.shape[1])
        return result
