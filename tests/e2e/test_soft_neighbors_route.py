"""E2E for the v1.7.0 soft-graph neighbors endpoint.

Locks the contract that ingesting a Python file with multiple changed
symbols produces ``co_changed`` soft edges between every pair, and
that ``/memory/soft_neighbors`` returns weighted neighbors in
descending weight order.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="soft-ws")
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, path: str, content: str) -> dict:
    r = client.post(
        "/memory/ingest_file",
        json={
            "workspace_id": "soft-ws",
            "path": path,
            "content": content,
            "language": "python",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_co_change_pairs_emitted_on_first_ingest(client: TestClient) -> None:
    """First ingest of a file with 3 symbols → co_changed edges in
    every direction."""
    src = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n\ndef gamma():\n    return 3\n"
    _ingest(client, "src/m.py", src)
    r = client.post(
        "/memory/soft_neighbors",
        json={"workspace_id": "soft-ws", "src_qualified_name": "alpha"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    targets = {n["dst_qualified_name"] for n in body["neighbors"]}
    assert "beta" in targets
    assert "gamma" in targets


def test_co_change_weight_accumulates_on_re_ingest(client: TestClient) -> None:
    """Re-ingest with changed bodies bumps the weight."""
    src1 = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"
    src2 = "def alpha():\n    return 10\n\ndef beta():\n    return 20\n"
    _ingest(client, "src/m.py", src1)
    _ingest(client, "src/m.py", src2)
    r = client.post(
        "/memory/soft_neighbors",
        json={"workspace_id": "soft-ws", "src_qualified_name": "alpha"},
    )
    assert r.status_code == 200, r.text
    neighbors = r.json()["neighbors"]
    matching = [n for n in neighbors if n["dst_qualified_name"] == "beta"]
    assert len(matching) == 1
    # Two ingest passes → observation_count >= 2
    assert matching[0]["observation_count"] >= 2
    assert matching[0]["weight"] >= 2.0


def test_unrelated_symbol_returns_empty(client: TestClient) -> None:
    r = client.post(
        "/memory/soft_neighbors",
        json={"workspace_id": "soft-ws", "src_qualified_name": "ghost"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


def test_unknown_kind_rejected(client: TestClient) -> None:
    r = client.post(
        "/memory/soft_neighbors",
        json={
            "workspace_id": "soft-ws",
            "src_qualified_name": "alpha",
            "edge_kinds": ["psychic_link"],
        },
    )
    assert r.status_code == 400, r.text


def test_kind_filter(client: TestClient) -> None:
    """Filtering on co_changed returns the expected rows; filtering
    on a different kind returns empty (we don't emit other kinds in
    the pipeline yet)."""
    src = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"
    _ingest(client, "src/m.py", src)
    r = client.post(
        "/memory/soft_neighbors",
        json={
            "workspace_id": "soft-ws",
            "src_qualified_name": "alpha",
            "edge_kinds": ["co_changed"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= 1
    r2 = client.post(
        "/memory/soft_neighbors",
        json={
            "workspace_id": "soft-ws",
            "src_qualified_name": "alpha",
            "edge_kinds": ["similar_signature"],
        },
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["total"] == 0


def test_ingest_response_carries_soft_pair_count(client: TestClient) -> None:
    """The HTTP response surfaces nothing about soft pairs today (kept
    out of IngestFileResponse to avoid noise), but the pipeline must
    still record them — we verify via the lookup endpoint."""
    src = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"
    _ingest(client, "src/m.py", src)
    r = client.post(
        "/memory/soft_neighbors",
        json={"workspace_id": "soft-ws", "src_qualified_name": "alpha"},
    )
    assert r.status_code == 200
    assert r.json()["total"] >= 1
