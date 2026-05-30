"""MCP startup embedding warm-up (release plan step 10.3).

``_maybe_warm_embeddings`` spawns a background daemon thread at MCP startup
that loads the embedding model so the first tool call needing an embedding does
not pay the cold model-load. Gated by ``settings.mcp_warm_embed`` and
failure-soft (the warm-up must never crash the server).
"""

from __future__ import annotations

import builtins
import types
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
    # The real pre-import pulls the heavy scipy/sklearn stack; stub it so this
    # unit test stays hermetic. Its own behaviour is covered separately below.
    monkeypatch.setattr(srv, "_preimport_embedding_stack", lambda *_a, **_k: None)
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


def _import_spy(seen: list[str], *, raise_for_st: bool = False):
    """Build an ``__import__`` replacement that records (and optionally fails)
    ``import sentence_transformers`` while delegating every other import."""
    real_import = builtins.__import__

    def _spy(name: str, *args: Any, **kw: Any) -> Any:
        if name == "sentence_transformers":
            seen.append(name)
            if raise_for_st:
                raise ImportError("boom")
            return types.ModuleType("sentence_transformers")
        return real_import(name, *args, **kw)

    return _spy


def test_preimport_skipped_for_ollama_backend(settings_factory, monkeypatch) -> None:
    # The heavy ST stack (scipy/sklearn) must never be imported when the backend
    # is ollama -- there is no local model to warm.
    settings = settings_factory(EMBEDDING_BACKEND="ollama", EMBEDDING_BASE_URL="http://x")
    seen: list[str] = []
    monkeypatch.setattr(builtins, "__import__", _import_spy(seen))
    srv._preimport_embedding_stack(settings)
    assert seen == []


def test_preimport_imports_st_for_default_backend(settings_factory, monkeypatch) -> None:
    # Regression for the first-call hang: the heavy ``import sentence_transformers``
    # (which pulls scipy.stats + sklearn) must be forced on the calling/main
    # thread -- not left to the warm-up daemon thread, where on Python 3.14 it
    # can deadlock on the import lock and wedge ``_load_lock`` forever.
    settings = settings_factory()  # default EMBEDDING_BACKEND=sentence_transformers
    seen: list[str] = []
    monkeypatch.setattr(builtins, "__import__", _import_spy(seen))
    srv._preimport_embedding_stack(settings)
    assert seen == ["sentence_transformers"]


def test_preimport_is_failure_soft(settings_factory, monkeypatch) -> None:
    # A broken or missing stack must not stop the server from starting.
    settings = settings_factory()
    seen: list[str] = []
    monkeypatch.setattr(builtins, "__import__", _import_spy(seen, raise_for_st=True))
    srv._preimport_embedding_stack(settings)  # must not raise
    assert seen == ["sentence_transformers"]
