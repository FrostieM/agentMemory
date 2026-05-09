"""E2E for /memory/code_graph and /ui/graph (v2.1.2)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="grph-ws")
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, path: str, content: str) -> None:
    r = client.post(
        "/memory/ingest_file",
        json={
            "workspace_id": "grph-ws",
            "path": path,
            "content": content,
            "language": "python",
        },
    )
    assert r.status_code == 200, r.text


def test_overview_mode_returns_top_connected_symbols(client: TestClient) -> None:
    """No center → top-K most-connected nodes returned."""
    src = (
        "def helper():\n    return 1\n\n"
        "def caller():\n    helper()\n    helper()\n\n"
        "class Service:\n"
        "    def fetch(self):\n"
        "        helper()\n"
    )
    _ingest(client, "src/m.py", src)
    r = client.get("/memory/code_graph", params={"workspace_id": "grph-ws"})
    assert r.status_code == 200, r.text
    body = r.json()
    qns = {n["qualified_name"] for n in body["nodes"]}
    assert "helper" in qns
    assert body["truncated"] is False


def test_center_mode_bfs_includes_neighbors(client: TestClient) -> None:
    """With center=helper → its callers (caller, Service.fetch) appear."""
    src = (
        "def helper():\n    return 1\n\n"
        "def caller():\n    helper()\n\n"
        "class Service:\n"
        "    def fetch(self):\n"
        "        helper()\n"
    )
    _ingest(client, "src/m.py", src)
    r = client.get(
        "/memory/code_graph",
        params={"workspace_id": "grph-ws", "center": "helper", "depth": 1},
    )
    assert r.status_code == 200, r.text
    qns = {n["qualified_name"] for n in r.json()["nodes"]}
    assert "helper" in qns
    assert "caller" in qns
    assert "Service.fetch" in qns


def test_max_nodes_truncates(client: TestClient) -> None:
    """max_nodes cap respected, truncated flag set."""
    src = "\n\n".join(f"def fn{i}():\n    fn0()" for i in range(10))
    _ingest(client, "src/m.py", src)
    r = client.get(
        "/memory/code_graph",
        params={"workspace_id": "grph-ws", "center": "fn0", "depth": 5, "max_nodes": 3},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["nodes"]) <= 3
    # With max_nodes=3 and >3 callers of fn0, truncated must be True.
    assert body["truncated"] is True


def test_unknown_edge_kind_rejected(client: TestClient) -> None:
    r = client.get(
        "/memory/code_graph",
        params={"workspace_id": "grph-ws", "edge_kinds": "psychic_link"},
    )
    assert r.status_code == 400, r.text


def test_edge_kinds_filter(client: TestClient) -> None:
    """Filtering on extends should drop the calls edges."""
    src = "class Base: pass\nclass Child(Base): pass\ndef caller():\n    Child()\n"
    _ingest(client, "src/m.py", src)
    r = client.get(
        "/memory/code_graph",
        params={
            "workspace_id": "grph-ws",
            "center": "Child",
            "depth": 1,
            "edge_kinds": "extends",
        },
    )
    assert r.status_code == 200, r.text
    edges = r.json()["links"]
    assert all(e["edge_type"] == "extends" for e in edges)


def test_empty_workspace_returns_empty(client: TestClient) -> None:
    r = client.get("/memory/code_graph", params={"workspace_id": "grph-ws"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["nodes"] == []
    assert body["links"] == []


def test_ui_graph_html_served(client: TestClient) -> None:
    r = client.get("/ui/graph")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert "code graph" in r.text.lower()
    # The dashboard fetches /memory/code_graph
    assert "/memory/code_graph" in r.text
    # 2.2 (Phase 2.4): D3 is now vendored locally at /ui/vendor/<file>
    # instead of pulled from a CDN. Restores the local-only philosophy
    # and removes the SRI-mismatch failure mode discovered in 2.1.5.
    assert "/ui/vendor/d3.v7.8.5.min.js" in r.text
    # No CDN URLs in the rendered page anymore.
    assert "cdn.jsdelivr.net" not in r.text


def test_ui_vendor_d3_served(client: TestClient) -> None:
    """The local D3 bundle must be reachable at /ui/vendor/d3.v7.8.5.min.js
    so graph.html's bare <script src> resolves on first paint."""
    r = client.get("/ui/vendor/d3.v7.8.5.min.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    # File should be the real bundle — d3 is ~270 KB minified.
    assert len(r.content) > 100_000
    # And the global object name appears in the source.
    assert b"d3" in r.content[:5000]
