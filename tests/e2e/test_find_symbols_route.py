"""E2E for /memory/find_symbols — symbol-level chunk lookup.

1.4.0: locks the contract that ingesting a code file via
``POST /memory/ingest_file`` populates ``chunks.qualified_name`` /
``chunks.symbol_kind`` for every structural decl, and that
``POST /memory/find_symbols`` then exact-matches by qualified name,
prefix-matches by qualified-name prefix, filters by symbol kind, and
filters by language.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_memory_lite.chunking.ts_grammar import is_supported

PY_SOURCE = '''\
"""mod docstring"""


def alpha(x):
    return x + 1


class Beta:
    def gamma(self):
        return 42

    def delta(self):
        return 7
'''


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="sym-ws")
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, path: str, content: str, language: str) -> None:
    r = client.post(
        "/memory/ingest_file",
        json={
            "workspace_id": "sym-ws",
            "path": path,
            "content": content,
            "language": language,
        },
    )
    assert r.status_code == 200, r.text


def test_python_exact_match(client: TestClient) -> None:
    _ingest(client, "src/foo.py", PY_SOURCE, "python")
    r = client.post(
        "/memory/find_symbols",
        json={"workspace_id": "sym-ws", "name": "Beta.gamma"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    hit = body["hits"][0]
    assert hit["qualified_name"] == "Beta.gamma"
    assert hit["symbol_kind"] == "method"
    assert hit["parent_qualified_name"] == "Beta"
    assert hit["language"] == "python"
    assert hit["path"] == "src/foo.py"
    assert "def gamma" in hit["text"]


def test_prefix_match_lists_methods(client: TestClient) -> None:
    _ingest(client, "src/foo.py", PY_SOURCE, "python")
    r = client.post(
        "/memory/find_symbols",
        json={"workspace_id": "sym-ws", "name_prefix": "Beta."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    qualnames = sorted(h["qualified_name"] for h in body["hits"])
    assert qualnames == ["Beta.delta", "Beta.gamma"]


def test_kind_filter(client: TestClient) -> None:
    _ingest(client, "src/foo.py", PY_SOURCE, "python")
    r = client.post(
        "/memory/find_symbols",
        json={"workspace_id": "sym-ws", "kinds": ["class"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    qualnames = [h["qualified_name"] for h in body["hits"]]
    assert qualnames == ["Beta"]


def test_unknown_kind_rejected(client: TestClient) -> None:
    r = client.post(
        "/memory/find_symbols",
        json={"workspace_id": "sym-ws", "kinds": ["macro"]},
    )
    # 422 is FastAPI's validation failure default; the body carries
    # the ValueError raised inside the route handler.
    assert r.status_code in (400, 422, 500), r.text


def test_typescript_dispatch(client: TestClient) -> None:
    if not is_supported("typescript"):
        pytest.skip("tree-sitter-typescript not installed")
    src = (
        "interface User { id: number; }\n"
        "class Service {\n"
        "  fetch(id: number): User { return { id }; }\n"
        "}\n"
    )
    _ingest(client, "src/svc.ts", src, "typescript")
    r = client.post(
        "/memory/find_symbols",
        json={"workspace_id": "sym-ws", "name": "Service.fetch"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    hit = body["hits"][0]
    assert hit["symbol_kind"] == "method"
    assert hit["language"] == "typescript"


def test_isolated_workspace(client: TestClient) -> None:
    """find_symbols MUST not leak rows from a different workspace_id."""
    _ingest(client, "src/foo.py", PY_SOURCE, "python")
    r = client.post(
        "/memory/find_symbols",
        json={"workspace_id": "other-ws", "name_prefix": "Beta"},
    )
    # Reading a different workspace is allowed; it should just return zero.
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0
