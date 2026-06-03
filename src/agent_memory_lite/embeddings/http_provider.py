"""HTTP-backed ``EmbeddingProvider`` -- delegate to the agent-memory-lite service.

The MCP stdio server uses this so it NEVER imports sentence-transformers / torch
in its own process. Importing torch before ``_server.run()`` forces an 8-15s
connect (the model load blocks the MCP handshake); deferring it until serving
deadlocks Python 3.14's import lock while the event loop is active. Both were
measured. Delegating embeddings to the already-running, already-warm HTTP
service (``POST /embed``) keeps the MCP server a thin, torch-free client: it
connects in well under a second and frequent reconnects stay instant.

Vectors are identical to in-process embedding because the service applies the
same model + e5 ``kind`` prefixing on its side. ``stdio_runtime`` falls back to
the in-process provider when the service is unreachable (probed once at start).
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
PROBE_TIMEOUT_SECONDS = 2.0


class HttpEmbeddingProvider(EmbeddingProvider):
    """Calls ``<base_url>/embed`` on the local agent-memory-lite HTTP service."""

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
        return f"http:{self._model_name}"

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = int(self._post_embed(["dimension probe"], "doc").shape[1])
        return self._dim

    def probe(self) -> None:
        """Confirm /embed is reachable AND present, used once at MCP startup to
        choose HTTP vs in-process. We POST /embed (not GET /health) on purpose:
        an OLDER service build answers /health but has no /embed, and choosing
        HTTP there would 404 every episode write. A short timeout keeps the
        connect path fast when the service is down -- the caller then falls back
        to in-process embedding."""
        try:
            with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS, trust_env=False) as client:
                response = client.post(
                    f"{self._base_url}/embed", json={"texts": ["probe"], "kind": "doc"}
                )
                response.raise_for_status()
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # InvalidURL (a malformed MEMORY_HTTP_BASE_URL) is NOT an HTTPError
            # subclass; catch it too so a bad env var falls back to in-process
            # embedding instead of crashing the server before it serves.
            raise EmbeddingProviderUnavailableError(
                f"agent-memory-lite /embed unavailable at {self._base_url}: {exc}"
            ) from exc

    def _post_embed(self, texts: list[str], kind: str) -> np.ndarray:
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                response = client.post(
                    f"{self._base_url}/embed", json={"texts": texts, "kind": kind}
                )
                response.raise_for_status()
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise EmbeddingProviderUnavailableError(
                f"agent-memory-lite /embed call failed at {self._base_url}: {exc}"
            ) from exc
        data = response.json()
        vectors = data.get("vectors") if isinstance(data, dict) else None
        if not isinstance(vectors, list) or not vectors:
            raise EmbeddingProviderUnavailableError(
                f"/embed response missing 'vectors': {data!r}"
            )
        return np.asarray(vectors, dtype=np.float32)

    def embed_batch(self, texts: list[str], *, kind: EmbeddingKind = "doc") -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        chunks: list[np.ndarray] = [
            self._post_embed(batch, kind) for batch in iter_batches(texts, self._batch_size)
        ]
        result = np.vstack(chunks)
        if self._dim is None:
            self._dim = int(result.shape[1])
        return result
