"""``POST /embed`` -- the endpoint the MCP server's HttpEmbeddingProvider calls.

Uses the app_factory fixture, which overrides the embedding dependency with a
deterministic fake, so the route is tested without loading a real model.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_embed_returns_vectors_matching_dim(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.post("/embed", json={"texts": ["hello", "world"], "kind": "doc"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["vectors"]) == 2
    assert data["dim"] > 0
    assert all(len(v) == data["dim"] for v in data["vectors"])
    assert data["name"]


def test_embed_empty_texts_returns_no_vectors(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.post("/embed", json={"texts": []})
    assert resp.status_code == 200
    data = resp.json()
    assert data["vectors"] == []
    assert data["dim"] > 0  # falls back to provider.dim for an empty batch


def test_embed_kind_defaults_to_doc(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.post("/embed", json={"texts": ["x"]})  # kind omitted
    assert resp.status_code == 200
    assert len(resp.json()["vectors"]) == 1
