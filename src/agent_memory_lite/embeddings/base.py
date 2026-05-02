"""Embedding provider contract.

Two providers ship with v1:
- `SentenceTransformersProvider` — default, runs locally on CPU.
- `OllamaProvider` — opt-in, calls a local Ollama instance.

Both produce L2-normalized float32 vectors of a fixed `dim`.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

import numpy as np

EmbeddingKind = Literal["doc", "query"]


class EmbeddingProviderUnavailableError(RuntimeError):
    """Raised when a configured provider cannot be reached or initialised."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol every embedding backend must implement."""

    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed_batch(self, texts: list[str], *, kind: EmbeddingKind = "doc") -> np.ndarray: ...
