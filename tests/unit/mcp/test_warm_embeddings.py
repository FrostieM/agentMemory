"""MCP startup embedding warm-up (release plan step 10.3).

``_maybe_warm_embeddings`` spawns a background daemon thread at MCP startup
that loads the embedding model so the first tool call needing an embedding does
not pay the cold model-load. Gated by ``settings.mcp_warm_embed`` and
failure-soft (the warm-up must never crash the server).
"""

from __future__ import annotations

from typing import Any

import agent_memory_lite.mcp.stdio_server as srv


class _FakeProvider:
    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    def embed_batch(self, texts: list[str], **_kw: Any) -> None:
        self.seen.append(list(texts))


def test_maybe_warm_embeddings_disabled_returns_none(settings_factory) -> None:
    settings = settings_factory(MEMORY_MCP_WARM_EMBED="false")
    assert srv._maybe_warm_embeddings(settings) is None


def test_maybe_warm_embeddings_enabled_warms_provider(settings_factory, monkeypatch) -> None:
    fake = _FakeProvider()
    monkeypatch.setattr(srv._runtime, "provider", lambda: fake)
    settings = settings_factory(MEMORY_MCP_WARM_EMBED="true")

    thread = srv._maybe_warm_embeddings(settings)
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert fake.seen == [["warmup"]]  # warmed with a throwaway batch


def test_warm_embeddings_is_failure_soft(monkeypatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(srv._runtime, "provider", _boom)
    # Must not raise -- a warm-up failure can never crash the MCP server.
    srv._warm_embeddings()
