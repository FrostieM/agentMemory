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
    response = client.post(
        "/memory/ingest_file",
        json={
            "workspace_id": "dig-ws",
            "path": path,
            "content": content,
            "language": "python",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_impact_check_reads_digest_after_ingest(client: TestClient) -> None:
    src = "def alpha(): pass\n\nclass Beta:\n    def gamma(self): pass\n"
    _ingest(client, "src/m.py", src)

    response = client.get(
        "/memory/impact_check",
        params={"workspace_id": "dig-ws", "file_path": "src/m.py"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    digest = body["data"]["digest"]
    assert digest["file_path"] == "src/m.py"
    assert digest["language"] == "python"
    assert digest["symbol_count"] == 3
    assert digest["chunk_count"] == 3
    qnames = {item["name"] for item in digest["top_symbols"]}
    assert {"alpha", "Beta", "Beta.gamma"} <= qnames


def test_impact_check_unknown_file_is_failure_soft(client: TestClient) -> None:
    response = client.get(
        "/memory/impact_check",
        params={"workspace_id": "dig-ws", "file_path": "src/ghost.py"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["verdict"] == "not_indexed"
