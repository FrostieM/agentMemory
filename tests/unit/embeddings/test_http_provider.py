"""HttpEmbeddingProvider + MCP runtime provider selection.

The MCP server delegates embeddings to the HTTP service so it never imports
torch (fast connect, no Python-3.14 import deadlock). These tests cover the
client provider in isolation (mocked httpx) and the startup HTTP-vs-in-process
choice in ``_Runtime._build_provider``.
"""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np
import pytest

from agent_memory_lite.embeddings import http_provider as hp
from agent_memory_lite.embeddings.base import EmbeddingProviderUnavailableError
from agent_memory_lite.embeddings.http_provider import HttpEmbeddingProvider


class _FakeResp:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """Stands in for ``httpx.Client`` -- returns a canned /embed payload."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def post(self, _url: str, json: Any = None) -> _FakeResp:
        return _FakeResp(self._payload)

    def get(self, _url: str) -> _FakeResp:
        return _FakeResp({})


class _BoomClient:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> _BoomClient:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def get(self, _url: str) -> _FakeResp:
        raise httpx.ConnectError("service down")

    def post(self, _url: str, json: Any = None) -> _FakeResp:
        raise httpx.ConnectError("service down")


def test_embed_batch_parses_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"vectors": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], "dim": 3, "name": "x"}
    monkeypatch.setattr(hp.httpx, "Client", lambda *_a, **_k: _FakeClient(payload))
    provider = HttpEmbeddingProvider("http://127.0.0.1:8765", "e5-small")
    out = provider.embed_batch(["a", "b"], kind="doc")
    assert out.shape == (2, 3)
    assert out.dtype == np.float32


def test_embed_batch_empty_is_zero_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"vectors": [[0.0, 0.0]], "dim": 2}
    monkeypatch.setattr(hp.httpx, "Client", lambda *_a, **_k: _FakeClient(payload))
    provider = HttpEmbeddingProvider("http://127.0.0.1:8765", "e5-small")
    out = provider.embed_batch([])  # no HTTP call needed beyond dim discovery
    assert out.shape == (0, 2)


def test_probe_raises_when_service_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hp.httpx, "Client", lambda *_a, **_k: _BoomClient())
    provider = HttpEmbeddingProvider("http://127.0.0.1:8765", "e5-small")
    with pytest.raises(EmbeddingProviderUnavailableError):
        provider.probe()


def test_build_provider_uses_http_when_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_memory_lite.mcp import stdio_runtime  # noqa: PLC0415

    monkeypatch.setattr(HttpEmbeddingProvider, "probe", lambda _self: None)
    runtime = stdio_runtime._Runtime()
    provider = runtime._build_provider()
    assert isinstance(provider, HttpEmbeddingProvider)
    assert runtime.using_remote_embeddings() is True


def test_build_provider_falls_back_when_http_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_memory_lite.mcp import stdio_runtime  # noqa: PLC0415

    def _boom(_self: HttpEmbeddingProvider) -> None:
        raise EmbeddingProviderUnavailableError("down")

    monkeypatch.setattr(HttpEmbeddingProvider, "probe", _boom)
    runtime = stdio_runtime._Runtime()
    provider = runtime._build_provider()
    # Fell back to the in-process provider -- a standalone MCP server still works.
    assert not isinstance(provider, HttpEmbeddingProvider)
