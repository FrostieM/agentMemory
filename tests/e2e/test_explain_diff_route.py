"""E2E for /memory/explain_diff — declarative + substring matching.

Locks the v1.3.0 contract: decisions with explicit references_json
match by ``match='declarative'``; pre-1.3.0 decisions whose text
mentions a file path match by ``match='substring'``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="diff-ws")
    with TestClient(app) as c:
        yield c


def _write_decision(
    client: TestClient,
    title: str,
    text: str,
    *,
    references: list[str] | None = None,
) -> str:
    payload = {
        "title": title,
        "decision_text": text,
        "rationale": "test",
        "importance": 0.8,
    }
    if references is not None:
        payload["references"] = references
    r = client.post(
        "/memory/write",
        json={"workspace_id": "diff-ws", "kind": "decision", "payload": payload},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["decision_id"]


def test_explain_diff_declarative_match(client: TestClient) -> None:
    """1.3.0: decision with explicit references matches by 'declarative'."""
    _write_decision(
        client,
        title="Use exit_engine for tier ladder",
        text="The tier ladder logic lives in src/exit_engine.py and must remain there.",
        references=["src/exit_engine.py", "src/tier_ladder.py"],
    )
    r = client.post(
        "/memory/explain_diff",
        json={
            "workspace_id": "diff-ws",
            "files": ["src/exit_engine.py"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] == ["src/exit_engine.py"]
    assert len(body["decisions_matched"]) == 1
    match = body["decisions_matched"][0]
    assert match["match"] == "declarative"
    assert match["matched_path"] == "src/exit_engine.py"


def test_explain_diff_substring_fallback(client: TestClient) -> None:
    """A decision without explicit references still matches via substring
    fallback when its decision_text mentions the file path."""
    _write_decision(
        client,
        title="Logging policy for selector_gate",
        text="Selector gate logging in src/selector_gate.py must be at INFO level not DEBUG.",
        references=[],
    )
    r = client.post(
        "/memory/explain_diff",
        json={
            "workspace_id": "diff-ws",
            "files": ["src/selector_gate.py"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["decisions_matched"]) == 1
    assert body["decisions_matched"][0]["match"] == "substring"


def test_explain_diff_extracts_files_from_unified_diff(client: TestClient) -> None:
    """When ``diff_text`` is provided, file paths are pulled from
    ``+++ b/<path>`` headers."""
    _write_decision(
        client,
        title="Watch src/api.py",
        text="src/api.py is the canonical surface.",
        references=["src/api.py"],
    )
    diff = (
        "diff --git a/src/api.py b/src/api.py\n"
        "index 0000..1111 100644\n"
        "--- a/src/api.py\n"
        "+++ b/src/api.py\n"
        "@@ -1 +1,2 @@\n"
        "+new line\n"
    )
    r = client.post(
        "/memory/explain_diff",
        json={"workspace_id": "diff-ws", "diff_text": diff},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] == ["src/api.py"]
    assert len(body["decisions_matched"]) == 1


def test_explain_diff_empty_input_returns_empty_list(client: TestClient) -> None:
    """No files in input → no matches, summary explains the situation."""
    r = client.post(
        "/memory/explain_diff",
        json={"workspace_id": "diff-ws", "diff_text": ""},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["files"] == []
    assert body["decisions_matched"] == []
    assert "no file paths" in body["summary"]
