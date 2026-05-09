"""E2E for /memory/graph_neighbors — hard-graph upstream / downstream lookup.

1.5.0: locks the contract that ingesting a Python file via
``POST /memory/ingest_file`` produces ``symbol_edges`` rows the
``/memory/graph_neighbors`` endpoint can return both directionally.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

PY_SOURCE = '''\
"""mod"""
from typing import Any


def helper(x):
    return x + 1


class Service:
    def fetch(self):
        helper(1)
        return Inner()


class Inner:
    pass
'''


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="graph-ws")
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, path: str, content: str) -> None:
    r = client.post(
        "/memory/ingest_file",
        json={
            "workspace_id": "graph-ws",
            "path": path,
            "content": content,
            "language": "python",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["chunks_written"] > 0


def test_upstream_helper_called_by_service_fetch(client: TestClient) -> None:
    _ingest(client, "src/svc.py", PY_SOURCE)
    r = client.post(
        "/memory/graph_neighbors",
        json={
            "workspace_id": "graph-ws",
            "qualified_name": "helper",
            "direction": "upstream",
            "edge_types": ["calls"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    callers = {e["src_qualified_name"] for e in body["upstream"]}
    assert "Service.fetch" in callers


def test_downstream_service_fetch_uses_helper_and_inner(client: TestClient) -> None:
    _ingest(client, "src/svc.py", PY_SOURCE)
    # Find Service.fetch chunk_id via /memory/find_symbols
    r = client.post(
        "/memory/find_symbols",
        json={"workspace_id": "graph-ws", "name": "Service.fetch"},
    )
    assert r.status_code == 200, r.text
    chunk_id = r.json()["hits"][0]["chunk_id"]

    r2 = client.post(
        "/memory/graph_neighbors",
        json={
            "workspace_id": "graph-ws",
            "chunk_id": chunk_id,
            "direction": "downstream",
        },
    )
    assert r2.status_code == 200, r2.text
    targets = {e["dst_qualified_name"] for e in r2.json()["downstream"]}
    # 'helper' is a calls edge; 'Inner' is an instantiates edge
    assert "helper" in targets
    assert "Inner" in targets


def test_extends_edge_in_upstream(client: TestClient) -> None:
    src = "class Base:\n    pass\nclass Child(Base):\n    pass\n"
    _ingest(client, "src/inh.py", src)
    r = client.post(
        "/memory/graph_neighbors",
        json={
            "workspace_id": "graph-ws",
            "qualified_name": "Base",
            "edge_types": ["extends"],
            "direction": "upstream",
        },
    )
    assert r.status_code == 200, r.text
    callers = {e["src_qualified_name"] for e in r.json()["upstream"]}
    assert callers == {"Child"}


def test_imports_edge_attached_to_module(client: TestClient) -> None:
    """`from typing import Any` becomes an upstream edge for typing.Any."""
    _ingest(client, "src/svc.py", PY_SOURCE)
    r = client.post(
        "/memory/graph_neighbors",
        json={
            "workspace_id": "graph-ws",
            "qualified_name": "typing.Any",
            "edge_types": ["imports"],
            "direction": "upstream",
        },
    )
    assert r.status_code == 200, r.text
    inbound = r.json()["upstream"]
    assert len(inbound) >= 1
    # src_qualified_name is the synthetic '<module>' anchor
    assert all(e["src_qualified_name"] == "<module>" for e in inbound)


def test_unknown_edge_type_rejected(client: TestClient) -> None:
    r = client.post(
        "/memory/graph_neighbors",
        json={
            "workspace_id": "graph-ws",
            "qualified_name": "helper",
            "edge_types": ["uses_psychic_powers"],
        },
    )
    assert r.status_code == 400, r.text


def test_missing_target_rejected(client: TestClient) -> None:
    r = client.post(
        "/memory/graph_neighbors",
        json={"workspace_id": "graph-ws"},
    )
    assert r.status_code == 400, r.text


def test_re_ingest_drops_stale_edges(client: TestClient) -> None:
    """Re-ingesting a file with new content should drop edges that
    pointed at the old chunks. This locks the cleanup invariant."""
    src_v1 = "def foo():\n    bar()\n"
    src_v2 = "def foo():\n    baz()\n"
    _ingest(client, "src/v.py", src_v1)
    _ingest(client, "src/v.py", src_v2)
    r = client.post(
        "/memory/graph_neighbors",
        json={
            "workspace_id": "graph-ws",
            "qualified_name": "bar",
            "edge_types": ["calls"],
            "direction": "upstream",
        },
    )
    assert r.status_code == 200, r.text
    # bar() is gone in v2; the v1 edge must not survive
    assert r.json()["upstream"] == []
    r2 = client.post(
        "/memory/graph_neighbors",
        json={
            "workspace_id": "graph-ws",
            "qualified_name": "baz",
            "edge_types": ["calls"],
            "direction": "upstream",
        },
    )
    assert r2.status_code == 200, r2.text
    assert {e["src_qualified_name"] for e in r2.json()["upstream"]} == {"foo"}
