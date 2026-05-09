"""E2E for /memory/symbol_history + /memory/breaking_changes (1.6.0).

Locks the contract that re-ingesting a Python file with a changed
signature produces a new ``symbol_versions`` row, and that
``/memory/breaking_changes`` surfaces the diff with downstream caller
counts via the hard graph.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="ver-ws")
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, path: str, content: str) -> dict:
    r = client.post(
        "/memory/ingest_file",
        json={
            "workspace_id": "ver-ws",
            "path": path,
            "content": content,
            "language": "python",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_symbol_history_records_first_version(client: TestClient) -> None:
    body = _ingest(client, "src/a.py", "def foo(x):\n    return x + 1\n")
    assert body["versions_written"] >= 1
    r = client.post(
        "/memory/symbol_history",
        json={"workspace_id": "ver-ws", "qualified_name": "foo"},
    )
    assert r.status_code == 200, r.text
    versions = r.json()["versions"]
    assert len(versions) == 1
    assert versions[0]["signature_text"] == "def foo(x):"


def test_symbol_history_appends_on_content_change(client: TestClient) -> None:
    _ingest(client, "src/a.py", "def foo(x):\n    return x + 1\n")
    _ingest(client, "src/a.py", "def foo(x):\n    return x + 2\n")
    r = client.post(
        "/memory/symbol_history",
        json={"workspace_id": "ver-ws", "qualified_name": "foo"},
    )
    assert r.status_code == 200, r.text
    versions = r.json()["versions"]
    assert len(versions) == 2
    # signature unchanged → both versions have the same signature_text
    assert versions[0]["signature_text"] == versions[1]["signature_text"]
    # content_hash differs
    assert versions[0]["content_hash"] != versions[1]["content_hash"]


def test_symbol_history_idempotent_on_unchanged_content(client: TestClient) -> None:
    body1 = _ingest(client, "src/a.py", "def foo(x):\n    return x + 1\n")
    body2 = _ingest(client, "src/a.py", "def foo(x):\n    return x + 1\n")
    # Second ingest is a content_hash hit on files → entire ingest skipped
    assert body2["skipped"] is True
    assert body2["versions_written"] == 0
    # History still has just 1 row
    r = client.post(
        "/memory/symbol_history",
        json={"workspace_id": "ver-ws", "qualified_name": "foo"},
    )
    assert r.json()["total"] == 1
    _ = body1


def test_breaking_changes_surfaces_signature_diff(client: TestClient) -> None:
    """1.6.0: change a function's signature; breaking_changes shows the diff."""
    _ingest(client, "src/a.py", "def foo(x):\n    return x + 1\n")
    _ingest(client, "src/a.py", "def foo(x, y):\n    return x + y\n")
    r = client.post(
        "/memory/breaking_changes",
        json={"workspace_id": "ver-ws", "since_days": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    matching = [c for c in body["changes"] if c["qualified_name"] == "foo"]
    assert len(matching) == 1
    change = matching[0]
    assert change["prev_signature"] == "def foo(x):"
    assert change["new_signature"] == "def foo(x, y):"


def test_breaking_changes_caller_count_via_graph(client: TestClient) -> None:
    """The caller_count field uses /memory/graph_neighbors edges so an
    agent can see how dangerous a signature change is."""
    # File A defines foo; File B calls foo
    _ingest(client, "src/a.py", "def foo(x):\n    return x + 1\n")
    _ingest(client, "src/b.py", "from a import foo\n\ndef use():\n    return foo(1)\n")
    # Now change foo's signature
    _ingest(client, "src/a.py", "def foo(x, y):\n    return x + y\n")

    r = client.post(
        "/memory/breaking_changes",
        json={"workspace_id": "ver-ws", "since_days": 1, "include_callers": True},
    )
    assert r.status_code == 200, r.text
    matching = [c for c in r.json()["changes"] if c["qualified_name"] == "foo"]
    assert len(matching) == 1
    # use() calls foo() → caller_count is at least 1
    assert matching[0]["caller_count"] >= 1


def test_breaking_changes_skips_non_signature_edits(client: TestClient) -> None:
    """Body edit without signature change shouldn't appear in breaking_changes."""
    _ingest(client, "src/a.py", "def foo(x):\n    return x + 1\n")
    _ingest(client, "src/a.py", "def foo(x):\n    return x + 2\n")
    r = client.post(
        "/memory/breaking_changes",
        json={"workspace_id": "ver-ws", "since_days": 1},
    )
    assert r.status_code == 200, r.text
    qnames = {c["qualified_name"] for c in r.json()["changes"]}
    assert "foo" not in qnames


def test_symbol_history_unknown_returns_empty(client: TestClient) -> None:
    r = client.post(
        "/memory/symbol_history",
        json={"workspace_id": "ver-ws", "qualified_name": "nonexistent"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0
