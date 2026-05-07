"""E2E for GET /memory/telemetry — audit-log search/write rate metrics.

Added in 1.2.4 to give operators an objective measurement of whether
the seed search-discipline behavior_instruction shifted agent
behaviour. The route reads ``audit_log`` and partitions actions into
search vs write buckets.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, text: str) -> None:
    r = client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "project-a",
            "source_type": "agent_action",
            "raw_text": text,
            "trust_level": "agent_observed",
        },
    )
    assert r.status_code == 200, r.text


def _search(client: TestClient, query: str) -> None:
    r = client.post(
        "/memory/search",
        json={"workspace_id": "project-a", "query": query, "mode": "fts", "limit": 5},
    )
    assert r.status_code == 200, r.text


def test_telemetry_partitions_search_and_write(client: TestClient) -> None:
    """1.2.4 lock: ``search_total`` and ``write_total`` populate from
    audit_log into the right buckets, and ``search_per_write_ratio``
    is a deterministic float.
    """
    _ingest(client, "first episode for telemetry test")
    _ingest(client, "second episode for telemetry test")
    _search(client, "telemetry")
    _search(client, "first")
    _search(client, "second")

    r = client.get("/memory/telemetry?workspace_id=project-a&days=1")
    assert r.status_code == 200, r.text
    body = r.json()

    # Two ingests landed → write_total >= 2.
    assert body["write_total"] >= 2
    # Three searches landed → search_total >= 3.
    assert body["search_total"] >= 3
    # Ratio = search/max(write,1). With 3 searches / 2 writes that is
    # >= 1.5; the route returns 3 decimal places.
    assert body["search_per_write_ratio"] >= 1.0
    # by_action_top is sorted by count desc and contains the actions
    # we just exercised.
    actions = {row["action"] for row in body["by_action_top"]}
    assert "search" in actions or "memory_search" in actions
    assert "ingest_episode" in actions


def test_telemetry_per_day_aggregation_is_sorted(client: TestClient) -> None:
    """``per_day`` is grouped by YYYY-MM-DD prefix of created_at and
    sorted ascending by date so plotting/visualisation is trivial."""
    _ingest(client, "another episode for per-day test")
    _search(client, "another")
    r = client.get("/memory/telemetry?workspace_id=project-a&days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    dates = [row["date"] for row in body["per_day"]]
    assert dates == sorted(dates)
    # Each row carries integer counts.
    for row in body["per_day"]:
        assert isinstance(row["search"], int)
        assert isinstance(row["write"], int)


def test_telemetry_empty_window_returns_zero_counts(client: TestClient) -> None:
    """A window in the future where no audit rows exist returns
    zero counts, ratio 0.0, empty per_day list — no errors."""
    r = client.get(
        "/memory/telemetry?workspace_id=project-a"
        "&since=2099-01-01T00:00:00Z&until=2099-01-02T00:00:00Z"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["search_total"] == 0
    assert body["write_total"] == 0
    assert body["search_per_write_ratio"] == 0.0
    assert body["per_day"] == []
    assert body["by_action_top"] == []


def test_telemetry_excludes_bookkeeping_actions(client: TestClient) -> None:
    """Internal sentinel/UI events are not counted as search OR write
    so the ratio reflects real agent behaviour, not background noise."""
    _ingest(client, "x" * 80)
    r = client.get("/memory/telemetry?workspace_id=project-a&days=1")
    assert r.status_code == 200, r.text
    body = r.json()
    actions = {row["action"] for row in body["by_action_top"]}
    assert "sentinel.run_recorded" not in actions
    assert "ui_event" not in actions
