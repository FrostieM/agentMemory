"""E2E for the v1.8.0 file digest endpoints."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="dig-ws")
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, path: str, content: str) -> dict:
    r = client.post(
        "/memory/ingest_file",
        json={
            "workspace_id": "dig-ws",
            "path": path,
            "content": content,
            "language": "python",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_digest_built_on_first_ingest(client: TestClient) -> None:
    src = "def alpha(): pass\n\nclass Beta:\n    def gamma(self): pass\n"
    _ingest(client, "src/m.py", src)
    r = client.post(
        "/memory/file_digest",
        json={"workspace_id": "dig-ws", "file_path": "src/m.py"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_path"] == "src/m.py"
    assert body["language"] == "python"
    # alpha (function) + Beta (class) + Beta.gamma (method) → 3 symbols
    assert body["symbol_count"] == 3
    assert body["chunk_count"] == 3
    assert "structured" in body
    qnames = body["structured"]["qualified_names"]
    assert "alpha" in qnames
    assert "Beta" in qnames
    assert "Beta.gamma" in qnames
    # Narrative is non-empty and mentions the path
    assert "src/m.py" in body["narrative"]
    assert "python" in body["narrative"]


def test_digest_updated_on_re_ingest(client: TestClient) -> None:
    _ingest(client, "src/m.py", "def alpha(): pass\n")
    r1 = client.post(
        "/memory/file_digest",
        json={"workspace_id": "dig-ws", "file_path": "src/m.py"},
    )
    initial_updated = r1.json()["updated_at"]
    # Re-ingest with one more symbol
    _ingest(client, "src/m.py", "def alpha(): pass\n\ndef beta(): pass\n")
    r2 = client.post(
        "/memory/file_digest",
        json={"workspace_id": "dig-ws", "file_path": "src/m.py"},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["symbol_count"] == 2
    assert body["updated_at"] >= initial_updated


def test_unknown_file_returns_404(client: TestClient) -> None:
    r = client.post(
        "/memory/file_digest",
        json={"workspace_id": "dig-ws", "file_path": "src/ghost.py"},
    )
    assert r.status_code == 404, r.text


def test_list_digests_returns_workspace_overview(client: TestClient) -> None:
    _ingest(client, "src/a.py", "def x(): pass\n")
    _ingest(client, "src/b.py", "def y(): pass\n")
    _ingest(client, "src/c.py", "def z(): pass\n")
    r = client.post(
        "/memory/list_file_digests",
        json={"workspace_id": "dig-ws"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    paths = {d["file_path"] for d in body["digests"]}
    assert paths == {"src/a.py", "src/b.py", "src/c.py"}


def test_digest_edge_counts_reflect_graph(client: TestClient) -> None:
    """Ingest a file with internal calls; the digest should report
    outbound edges > 0."""
    src = "def helper(): pass\n\ndef caller():\n    helper()\n"
    _ingest(client, "src/m.py", src)
    r = client.post(
        "/memory/file_digest",
        json={"workspace_id": "dig-ws", "file_path": "src/m.py"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # caller() → helper() is one outbound edge from the file's
    # own qnames; helper is also the dst of that edge → inbound 1.
    assert body["outbound_edge_count"] >= 1
    assert body["inbound_edge_count"] >= 1


def test_digest_versions_recent_count(client: TestClient) -> None:
    _ingest(client, "src/m.py", "def alpha(): pass\n")
    r = client.post(
        "/memory/file_digest",
        json={"workspace_id": "dig-ws", "file_path": "src/m.py"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Just-ingested file → at least 1 version recorded in the last 7 days.
    assert body["versions_recent"] >= 1
