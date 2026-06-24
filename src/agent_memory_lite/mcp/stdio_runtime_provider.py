"""Embedding-provider resolution for the MCP stdio runtime.

Lifted out of ``stdio_runtime.py`` so that module stays under the
150-SLOC ceiling. ``_Runtime`` delegates its provider construction and
remote-provider probe to these free functions; behavior is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.mcp.stdio_env import _memory_http_base_url


def build_provider(
    settings: Settings,
    in_process_factory: Callable[[Settings], EmbeddingProvider] = get_embedding_provider,
) -> EmbeddingProvider:
    """Prefer the out-of-process HTTP embedder so this MCP process never
    imports torch -- importing it before serving forces an 8-15s connect and
    deferring it deadlocks Python 3.14's import lock (both measured; see
    http_provider). Probe the HTTP service once; fall back to the in-process
    provider when it is unreachable so a standalone MCP server still works.

    ``in_process_factory`` is injected so ``_Runtime`` can pass the
    ``stdio_runtime.get_embedding_provider`` module global, keeping that name
    monkeypatchable from the original module path."""
    from agent_memory_lite.embeddings.base import (  # noqa: PLC0415
        EmbeddingProviderUnavailableError,
    )
    from agent_memory_lite.embeddings.http_provider import (  # noqa: PLC0415
        HttpEmbeddingProvider,
    )

    base_url = _memory_http_base_url(settings)
    if base_url:
        candidate = HttpEmbeddingProvider(
            base_url=base_url,
            model_name=settings.embedding_model,
            batch_size=settings.embedding_batch_size,
        )
        try:
            candidate.probe()
        except EmbeddingProviderUnavailableError:
            pass
        else:
            return candidate
    return in_process_factory(settings)


def is_remote_provider(provider: EmbeddingProvider) -> bool:
    """True when ``provider`` is the out-of-process HTTP embedder, so
    stdio_server._run can skip the in-process warm-up and connect instantly."""
    from agent_memory_lite.embeddings.http_provider import (  # noqa: PLC0415
        HttpEmbeddingProvider,
    )

    return isinstance(provider, HttpEmbeddingProvider)
