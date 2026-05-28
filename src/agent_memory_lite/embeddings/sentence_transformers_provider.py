"""sentence-transformers backed `EmbeddingProvider`.

The first call lazily loads the model; subsequent calls reuse it. E5-style
prefixing (`query: ...` / `passage: ...`) is applied inside this provider so
callers stay clean.

Tests should never construct this provider directly — they should use the fake
provider exposed in `tests/conftest.py` to avoid downloading models.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from agent_memory_lite.embeddings.base import (
    EmbeddingKind,
    EmbeddingProvider,
    EmbeddingProviderUnavailableError,
)
from agent_memory_lite.embeddings.batching import iter_batches

DEFAULT_BATCH_SIZE = 32
_E5_PREFIXES = {"query": "query: ", "doc": "passage: "}


class SentenceTransformersProvider(EmbeddingProvider):
    def __init__(self, model_name: str, *, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._model: Any = None
        self._dim: int | None = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        except ImportError as exc:
            raise EmbeddingProviderUnavailableError(
                "sentence-transformers is not installed; install with `pip install -e .`"
            ) from exc
        # Local-only contract (config/local_only_guard.py): a model that is
        # already in the HF cache MUST load with zero network traffic.
        # ``SentenceTransformer`` WITHOUT ``local_files_only`` pings
        # huggingface.co on every load to verify the snapshot revision; that
        # call has NO app-level timeout, so a slow or blocked HF network
        # stalls the whole write path for minutes (observed as a hung
        # memory_write). Load cache-only first. Fall back to a networked
        # fetch ONLY when the model is genuinely absent -- the one-time
        # bootstrap, like ``ollama pull``. For a strictly air-gapped runtime
        # the cache-only path needs no extra env flag now.
        try:
            try:
                model = SentenceTransformer(self._model_name, local_files_only=True)
            except Exception:
                # Not cached yet -> one-time networked bootstrap fetch.
                model = SentenceTransformer(self._model_name)
        except Exception as exc:
            raise EmbeddingProviderUnavailableError(
                f"failed to load sentence-transformers model {self._model_name!r}: {exc}"
            ) from exc
        self._model = model
        self._dim = int(model.get_sentence_embedding_dimension())
        return self._model

    @property
    def name(self) -> str:
        return f"st:{self._model_name}"

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._load()
        assert self._dim is not None
        return self._dim

    def _prefix(self, text: str, kind: EmbeddingKind) -> str:
        if "e5" in self._model_name.lower():
            return _E5_PREFIXES[kind] + text
        return text

    def embed_batch(self, texts: list[str], *, kind: EmbeddingKind = "doc") -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        model = self._load()
        prefixed = [self._prefix(t, kind) for t in texts]
        chunks: list[np.ndarray] = []
        for batch in iter_batches(prefixed, self._batch_size):
            vectors = model.encode(
                batch,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            chunks.append(np.asarray(vectors, dtype=np.float32))
        return np.vstack(chunks)
