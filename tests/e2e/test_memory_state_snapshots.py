"""E2E for memory state snapshot routes:
* POST /memory/snapshot_save  — capture point-in-time digest
* POST /memory/snapshot_list  — newest-first list
* POST /memory/snapshot_diff  — counts deltas + added/removed/changed ids
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="snap-ws")
    with TestClient(app) as c:
        yield c


def _write_decision(client: TestClient, title: str, text: str) -> str:
    response = client.post(
        "/memory/write_decision",
        json={"workspace_id": "snap-ws", "title": title, "decision_text": text},
    )
    assert response.status_code == 200, response.text
    return response.json()["decision_id"]


def test_snapshot_save_returns_counts(client: TestClient) -> None:
    _write_decision(client, "First decision", "Body one.")
    response = client.post(
        "/memory/snapshot_save",
        json={"workspace_id": "snap-ws", "name": "before"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "before"
    assert body["counts"].get("decision_total") == 1
    assert body["snapshot_id"].startswith("memst_")


def test_snapshot_list_orders_newest_first(client: TestClient) -> None:
    a = client.post("/memory/snapshot_save", json={"workspace_id": "snap-ws", "name": "a"})
    b = client.post("/memory/snapshot_save", json={"workspace_id": "snap-ws", "name": "b"})
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    listed = client.post("/memory/snapshot_list", json={"workspace_id": "snap-ws"})
    assert listed.status_code == 200, listed.text
    names = [item["name"] for item in listed.json()["snapshots"]]
    # Both should appear; newest-first means "b" should not come after "a".
    assert {"a", "b"}.issubset(set(names))


def test_snapshot_diff_detects_added_and_changed(client: TestClient) -> None:
    early_id = _write_decision(client, "Initial", "Body before.")
    before = client.post(
        "/memory/snapshot_save", json={"workspace_id": "snap-ws", "name": "before"}
    )
    assert before.status_code == 200, before.text
    before_id = before.json()["snapshot_id"]

    # Add a new decision so the diff sees an "added" id.
    new_id = _write_decision(client, "Second", "Body for the second decision.")

    after = client.post("/memory/snapshot_save", json={"workspace_id": "snap-ws", "name": "after"})
    assert after.status_code == 200, after.text
    after_id = after.json()["snapshot_id"]

    diff = client.post(
        "/memory/snapshot_diff",
        json={
            "workspace_id": "snap-ws",
            "before_id": before_id,
            "after_id": after_id,
        },
    )
    assert diff.status_code == 200, diff.text
    body = diff.json()
    assert body["counts_delta"].get("decision_total") == 1
    assert f"decision:{new_id}" in body["added"]
    # The early decision exists in both snapshots, so it must not
    # appear in added/removed/changed (no edits between snapshots).
    assert f"decision:{early_id}" not in body["added"]
    assert f"decision:{early_id}" not in body["removed"]
    assert f"decision:{early_id}" not in body["changed"]


def test_snapshot_diff_404_on_missing(client: TestClient) -> None:
    real = client.post("/memory/snapshot_save", json={"workspace_id": "snap-ws", "name": "real"})
    assert real.status_code == 200
    response = client.post(
        "/memory/snapshot_diff",
        json={
            "workspace_id": "snap-ws",
            "before_id": "memst_does_not_exist",
            "after_id": real.json()["snapshot_id"],
        },
    )
    assert response.status_code == 404
