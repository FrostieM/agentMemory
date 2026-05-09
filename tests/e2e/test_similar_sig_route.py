"""E2E for the v2.1.4 similar_signature soft edge accumulator.

Locks the contract that ingesting two files with parallel function
signatures (``fetch_users(client) -> list[User]`` and
``fetch_orders(client) -> list[Order]``) produces a
``similar_signature`` soft edge that ``/memory/soft_neighbors``
returns.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    # Lower threshold for the test so the synthetic 6-token signatures
    # used here cross the bar. Production default 0.7 is calibrated
    # for typical 10-30 token signatures and stays untouched.
    app = app_factory(
        MEMORY_WORKSPACE_ID="sim-ws",
        MEMORY_SIMILAR_SIG_THRESHOLD="0.4",
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_off(app_factory) -> Iterator[TestClient]:
    app = app_factory(
        MEMORY_WORKSPACE_ID="sim-off-ws",
        MEMORY_SIMILAR_SIG_ENABLED="false",
    )
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, ws: str, path: str, content: str) -> None:
    r = client.post(
        "/memory/ingest_file",
        json={"workspace_id": ws, "path": path, "content": content, "language": "python"},
    )
    assert r.status_code == 200, r.text


def test_similar_signatures_emit_edge(client: TestClient) -> None:
    """Two parallel function signatures → similar_signature edge."""
    _ingest(
        client,
        "sim-ws",
        "src/users.py",
        "def fetch_users(client: Client) -> list[User]:\n    return client.get('users')\n",
    )
    _ingest(
        client,
        "sim-ws",
        "src/orders.py",
        "def fetch_orders(client: Client) -> list[Order]:\n    return client.get('orders')\n",
    )
    r = client.post(
        "/memory/soft_neighbors",
        json={
            "workspace_id": "sim-ws",
            "src_qualified_name": "fetch_orders",
            "edge_kinds": ["similar_signature"],
        },
    )
    assert r.status_code == 200, r.text
    targets = {n["dst_qualified_name"] for n in r.json()["neighbors"]}
    assert "fetch_users" in targets


def test_dissimilar_signatures_no_edge(client: TestClient) -> None:
    """Two unrelated signatures → no similar_signature edge."""
    _ingest(client, "sim-ws", "src/a.py", "class HttpClient: pass\n")
    _ingest(client, "sim-ws", "src/b.py", "def parse_xml(text): return text\n")
    r = client.post(
        "/memory/soft_neighbors",
        json={
            "workspace_id": "sim-ws",
            "src_qualified_name": "HttpClient",
            "edge_kinds": ["similar_signature"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


def test_flag_off_no_edges(client_off: TestClient) -> None:
    """With MEMORY_SIMILAR_SIG_ENABLED=false → no edges emitted."""
    _ingest(
        client_off,
        "sim-off-ws",
        "src/users.py",
        "def fetch_users(client: Client) -> list[User]:\n    return None\n",
    )
    _ingest(
        client_off,
        "sim-off-ws",
        "src/orders.py",
        "def fetch_orders(client: Client) -> list[Order]:\n    return None\n",
    )
    r = client_off.post(
        "/memory/soft_neighbors",
        json={
            "workspace_id": "sim-off-ws",
            "src_qualified_name": "fetch_orders",
            "edge_kinds": ["similar_signature"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


def test_bidirectional_edges(client: TestClient) -> None:
    """When fetch_users → fetch_orders is recorded, the inverse must
    also exist (mirrors v1.7 co_changed symmetry)."""
    _ingest(
        client,
        "sim-ws",
        "src/users.py",
        "def fetch_users(client: Client) -> list[User]:\n    return None\n",
    )
    _ingest(
        client,
        "sim-ws",
        "src/orders.py",
        "def fetch_orders(client: Client) -> list[Order]:\n    return None\n",
    )
    r1 = client.post(
        "/memory/soft_neighbors",
        json={
            "workspace_id": "sim-ws",
            "src_qualified_name": "fetch_users",
            "edge_kinds": ["similar_signature"],
        },
    )
    r2 = client.post(
        "/memory/soft_neighbors",
        json={
            "workspace_id": "sim-ws",
            "src_qualified_name": "fetch_orders",
            "edge_kinds": ["similar_signature"],
        },
    )
    targets_1 = {n["dst_qualified_name"] for n in r1.json()["neighbors"]}
    targets_2 = {n["dst_qualified_name"] for n in r2.json()["neighbors"]}
    assert "fetch_orders" in targets_1
    assert "fetch_users" in targets_2
