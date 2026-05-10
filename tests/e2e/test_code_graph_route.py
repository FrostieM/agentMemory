"""E2E for /memory/code_graph and /ui/graph (v2.1.2)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


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


# ---------- Phase 3.4: soft_edge_kinds param ----------


def _inject_soft_edge(
    db_path: object,
    *,
    workspace_id: str,
    src_qn: str,
    dst_qn: str,
    edge_kind: str,
    weight: float,
) -> None:
    """Seed a soft_edges row directly via SQL.

    The signature-similarity pipeline only emits a real
    ``similar_signature`` row when MinHash Jaccard ≥ threshold. Tests
    that just want to exercise the route's projection of weight +
    ``is_soft`` skip the pipeline and inject directly. ``db_path`` is
    the conftest's per-test sqlite file passed via fixture.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO soft_edges (id, workspace_id, src_qualified_name, "
            "dst_qualified_name, edge_kind, weight, observation_count, "
            "last_seen_at, created_at, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, '{}')",
            (
                new_id(IdKind.SOFT_EDGE),
                workspace_id,
                src_qn,
                dst_qn,
                edge_kind,
                weight,
                iso_now(),
                iso_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_soft_edges_excluded_by_default(client: TestClient, tmp_db_path) -> None:
    """Without ``soft_edge_kinds``, soft_edges rows are NOT in the
    response — the hard-edge contract is unchanged so existing
    consumers don't break."""
    src = "def alpha():\n    return 1\n\ndef beta():\n    alpha()\n"
    _ingest(client, "src/m.py", src)
    _inject_soft_edge(
        tmp_db_path,
        workspace_id="grph-ws",
        src_qn="alpha",
        dst_qn="beta",
        edge_kind="similar_signature",
        weight=0.85,
    )
    r = client.get(
        "/memory/code_graph",
        params={"workspace_id": "grph-ws", "center": "alpha", "depth": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    soft = [link for link in body["links"] if link.get("is_soft")]
    assert soft == [], f"unexpected soft links in default response: {soft}"


def test_soft_edges_included_with_param(client: TestClient, tmp_db_path) -> None:
    """``soft_edge_kinds=similar_signature`` includes the soft row in
    the response with ``is_soft=True`` and the original weight."""
    src = "def alpha():\n    return 1\n\ndef beta():\n    alpha()\n"
    _ingest(client, "src/m.py", src)
    _inject_soft_edge(
        tmp_db_path,
        workspace_id="grph-ws",
        src_qn="alpha",
        dst_qn="beta",
        edge_kind="similar_signature",
        weight=0.85,
    )
    r = client.get(
        "/memory/code_graph",
        params={
            "workspace_id": "grph-ws",
            "center": "alpha",
            "depth": 1,
            "soft_edge_kinds": "similar_signature",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    soft = [link for link in body["links"] if link.get("is_soft")]
    assert len(soft) >= 1, f"expected soft link, got links: {body['links']}"
    sl = soft[0]
    assert sl["edge_type"] == "similar_signature"
    assert sl["weight"] == 0.85
    assert sl["is_soft"] is True


def test_unknown_soft_edge_kind_rejected(client: TestClient) -> None:
    """Typo in ``soft_edge_kinds`` returns 400 with the allowed list."""
    r = client.get(
        "/memory/code_graph",
        params={"workspace_id": "grph-ws", "soft_edge_kinds": "telepathy"},
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "telepathy" in detail
    assert "similar_signature" in detail or "co_changed" in detail


def test_hard_edge_links_have_no_weight(client: TestClient) -> None:
    """Hard edges (calls / extends / etc.) leave ``weight=None`` and
    ``is_soft=False`` in the response so the renderer can branch on
    is_soft alone — no NaN weight values to defend against."""
    src = "def alpha():\n    return 1\n\ndef beta():\n    alpha()\n"
    _ingest(client, "src/m.py", src)
    r = client.get(
        "/memory/code_graph",
        params={"workspace_id": "grph-ws", "center": "alpha", "depth": 1},
    )
    assert r.status_code == 200, r.text
    for link in r.json()["links"]:
        assert link["weight"] is None, link
        assert link["is_soft"] is False, link


# ---------- Phase 3.6: /ui/browse + /ui/review pages ----------


def test_ui_browse_html_served(client: TestClient) -> None:
    """Browse page must serve, link the unified header script, and
    include the four kind options in the dropdown."""
    r = client.get("/ui/browse")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Browse" in body
    assert "/ui/app_header.js" in body
    # The four supported kinds must all appear as dropdown options.
    for kind in ("decisions", "theories", "insights", "behavior_instructions"):
        assert kind in body, f"missing kind '{kind}' in browse.html"
    # Nav strip must contain all five sibling pages so the operator
    # can hop without retyping URLs.
    for href in ("/ui", "/ui/code", "/ui/graph", "/ui/review", "/ui/browse"):
        assert href in body


def test_ui_review_html_served(client: TestClient) -> None:
    """Review page (Phase 3.3) must serve and reference the right
    promote/reject endpoints."""
    r = client.get("/ui/review")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Review" in body
    assert "/memory/list_candidates" in body
    assert "/memory/promote_candidate" in body
    assert "/memory/reject_candidate" in body
    assert "/memory/promote_candidate_to_behavior" in body
