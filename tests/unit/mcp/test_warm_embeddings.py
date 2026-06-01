"""MCP embedding warm-up.

``_warm_embeddings`` loads the embedding model SYNCHRONOUSLY on the main thread
at startup, before the stdio server serves. The sentence-transformers import
(scipy/sklearn) MUST run on the main thread -- in a daemon thread it deadlocks on
Python 3.14 while the event loop is active (py-spy showed the warm-up thread
wedged mid-import, hanging unrelated tool calls). Gated by
``settings.mcp_warm_embed`` and failure-soft (warm-up must never crash the
server).
"""

from __future__ import annotations

from typing import Any

import agent_memory_lite.mcp.stdio_server as srv


class _FakeProvider:
    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    def embed_batch(self, texts: list[str], **_kw: Any) -> None:
        self.seen.append(list(texts))


def test_warm_embeddings_disabled_is_noop(settings_factory, monkeypatch) -> None:
    fake = _FakeProvider()
    monkeypatch.setattr(srv._runtime, "provider", lambda: fake)
    settings = settings_factory(MEMORY_MCP_WARM_EMBED="false")
    srv._warm_embeddings(settings)
    assert fake.seen == []  # disabled -> provider never touched


def test_warm_embeddings_enabled_warms_provider(settings_factory, monkeypatch) -> None:
    fake = _FakeProvider()
    monkeypatch.setattr(srv._runtime, "provider", lambda: fake)
    settings = settings_factory(MEMORY_MCP_WARM_EMBED="true")
    srv._warm_embeddings(settings)
    assert fake.seen == [["warmup"]]  # warmed with a throwaway batch


def test_warm_embeddings_is_failure_soft(settings_factory, monkeypatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(srv._runtime, "provider", _boom)
    settings = settings_factory(MEMORY_MCP_WARM_EMBED="true")
    # Must not raise -- a warm-up failure can never crash the MCP server.
    srv._warm_embeddings(settings)
