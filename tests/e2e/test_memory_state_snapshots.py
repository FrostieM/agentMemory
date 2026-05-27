"""Canonical snapshot writes replace the old state-snapshot routes."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="snap-ws")
    with TestClient(app) as c:
        yield c


def test_memory_write_snapshot_returns_projection(client: TestClient) -> None:
    response = client.post(
        "/memory/write",
        json={
            "workspace_id": "snap-ws",
            "kind": "snapshot",
            "payload": {
                "snapshot_key": "daily-2026-05-26",
                "title": "Daily snapshot",
                "source_label": "test",
                "total_rows": 42,
                "metadata_json": '{"scope":"e2e"}',
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["kind"] == "snapshot"
    assert payload["data"]["snapshot_key"] == "daily-2026-05-26"
    assert payload["data"]["total_rows"] == 42
