"""``POST /embed`` -- raw embedding for the out-of-process MCP embedder.

The MCP stdio server delegates embeddings here (``HttpEmbeddingProvider``) so it
never imports torch in-process. The handler uses the service's own embedding
provider singleton, so the vectors are byte-for-byte what in-process embedding
would produce (same model + e5 ``kind`` prefixing).

Auth posture: this route is INTENTIONALLY mounted at root, not under
``/memory/*`` -- so it is exempt from the optional API-token guard and the
CSRF / workspace-routing middleware (all ``/memory/*``-scoped). The MCP client
sends no token and needs a stable, workspace-agnostic compute endpoint. It is
still protected by ``OriginGuardMiddleware`` (loopback ``Host`` only) and the
127.0.0.1 bind, and it neither reads nor writes memory -- it only returns a
vector for the supplied text.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent_memory_lite.api.deps import EmbeddingProviderDep
from agent_memory_lite.embeddings.base import EmbeddingKind

router = APIRouter()

# global-audit round-A: bound the batch. The 10MB body cap alone still admits tens
# of thousands of tiny strings, each spawning an embedding batch + vector allocation
# -- a same-host resource-exhaustion vector. A legitimate batch is far smaller.
_MAX_EMBED_TEXTS = 2048


class EmbedRequest(BaseModel):
    texts: list[str] = Field(max_length=_MAX_EMBED_TEXTS)
    kind: EmbeddingKind = "doc"


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    dim: int
    name: str


@router.post("/embed", response_model=EmbedResponse)
def embed(body: EmbedRequest, provider: EmbeddingProviderDep) -> EmbedResponse:
    vectors = provider.embed_batch(body.texts, kind=body.kind)
    return EmbedResponse(
        vectors=vectors.tolist(),
        dim=int(vectors.shape[1]) if vectors.ndim == 2 else provider.dim,
        name=provider.name,
    )
