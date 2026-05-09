"""E2E for /memory/code_overview — v2.0 dashboard payload."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="ovr-ws")
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, path: str, content: str) -> None:
    r = client.post(
        "/memory/ingest_file",
        json={
            "workspace_id": "ovr-ws",
            "path": path,
            "content": content,
            "language": "python",
        },
    )
    assert r.status_code == 200, r.text


def test_overview_empty_workspace(client: TestClient) -> None:
    r = client.get("/memory/code_overview", params={"workspace_id": "ovr-ws"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_id"] == "ovr-ws"
    assert body["counts"]["files"] == 0
    assert body["recent_files"] == []
    assert body["breaking"] == []
    assert body["active_edits"] == []


def test_overview_after_ingest(client: TestClient) -> None:
    src = "def helper(): pass\n\ndef caller():\n    helper()\n"
    _ingest(client, "src/m.py", src)
    r = client.get("/memory/code_overview", params={"workspace_id": "ovr-ws"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["files"] == 1
    assert body["counts"]["symbols"] == 2
    assert body["counts"]["edges"] >= 1
    assert body["counts"]["versions"] >= 2
    paths = [f["file_path"] for f in body["recent_files"]]
    assert "src/m.py" in paths
    # helper is called by caller → top_called includes it
    qnames = {h["qualified_name"] for h in body["top_called"]}
    assert "helper" in qnames


def test_overview_includes_breaking_change(client: TestClient) -> None:
    _ingest(client, "src/m.py", "def foo(x):\n    return x\n")
    _ingest(client, "src/m.py", "def foo(x, y):\n    return x + y\n")
    r = client.get(
        "/memory/code_overview",
        params={"workspace_id": "ovr-ws", "breaking_days": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    matching = [b for b in body["breaking"] if b["qualified_name"] == "foo"]
    assert len(matching) == 1
    assert matching[0]["prev_signature"] == "def foo(x):"
    assert matching[0]["new_signature"] == "def foo(x, y):"


def test_overview_includes_active_edits(client: TestClient) -> None:
    r = client.post(
        "/memory/claim_edit",
        json={
            "workspace_id": "ovr-ws",
            "agent_id": "claude",
            "qualified_name": "Service.fetch",
        },
    )
    assert r.status_code == 200, r.text

    r2 = client.get("/memory/code_overview", params={"workspace_id": "ovr-ws"})
    assert r2.status_code == 200, r2.text
    edits = r2.json()["active_edits"]
    assert any(e["agent_id"] == "claude" and e["qualified_name"] == "Service.fetch" for e in edits)


def test_overview_files_limit_respected(client: TestClient) -> None:
    for i in range(5):
        _ingest(client, f"src/f{i}.py", f"def fn{i}(): pass\n")
    r = client.get(
        "/memory/code_overview",
        params={"workspace_id": "ovr-ws", "files_limit": 3},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["recent_files"]) == 3


def test_ui_code_html_served(client: TestClient) -> None:
    """The /ui/code dashboard page is served as HTML."""
    r = client.get("/ui/code")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert "code overview" in r.text.lower()
    assert "/memory/code_overview" in r.text  # the JS calls this URL
