"""End-to-end tests for the optional `label` field added in migration 0015.

The contract:
  * Episodes and chunks accept an optional `label` (max 120 chars) at write
    time. The label is purely visual — FTS, vector search, scoring,
    ranking, and budget rendering still use `raw_text` / `text` only.
  * The label round-trips through the ingest pipeline and surfaces in
    `/memory/ui/state.recent[*].short_label` for observatory rendering.
  * Omitting the label keeps the legacy auto-derived display text.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, label: str | None = None, raw_text: str = "what happened") -> dict:
    body: dict[str, object] = {
        "workspace_id": "default",
        "source_type": "agent_action",
        "raw_text": raw_text,
        "trust_level": "agent_observed",
        "importance": 0.6,
    }
    if label is not None:
        body["label"] = label
    response = client.post("/memory/ingest_episode", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_ingest_accepts_label_and_round_trips_into_ui_state(client: TestClient) -> None:
    _ingest(client, label="Hook 400 fix", raw_text="long technical episode body")
    state = client.get("/memory/ui/state", params={"workspace_id": "default"}).json()
    rows = state["recent"]
    matching = [
        r for r in rows if r["table"] == "episodes" and r.get("short_label") == "Hook 400 fix"
    ]
    assert matching, [(r["table"], r.get("short_label"), r.get("label")) for r in rows[:5]]
    # The same label is mirrored to the chunk so chunk view also displays it.
    chunk_rows = [
        r for r in rows if r["table"] == "chunks" and r.get("short_label") == "Hook 400 fix"
    ]
    assert chunk_rows


def test_ingest_without_label_falls_back_to_auto_clip(client: TestClient) -> None:
    _ingest(client, label=None, raw_text="auto-clipped episode text without explicit label")
    state = client.get("/memory/ui/state", params={"workspace_id": "default"}).json()
    rows = state["recent"]
    episodes = [r for r in rows if r["table"] == "episodes"]
    assert episodes
    # Without an explicit label, short_label is None and `label` falls back
    # to the auto-clipped raw_text snippet.
    assert episodes[0].get("short_label") is None
    assert "auto-clipped" in (episodes[0]["label"] or "")


def test_label_does_not_affect_search_ranking(client: TestClient) -> None:
    """Searching for the label text alone must not match — FTS uses raw_text."""
    _ingest(
        client,
        label="UNIQ_LABEL_TOKEN_zzz",
        raw_text="completely unrelated episode body about retrieval pipelines",
    )
    response = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "UNIQ_LABEL_TOKEN_zzz",
            "limit": 5,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    hits = body["data"]
    # The label is purely visual: FTS over raw_text/text doesn't index it.
    assert hits == []


def test_label_max_length_120_chars(client: TestClient) -> None:
    too_long = "x" * 121
    response = client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "default",
            "source_type": "agent_action",
            "raw_text": "body",
            "label": too_long,
            "trust_level": "agent_observed",
            "importance": 0.5,
        },
    )
    assert response.status_code == 422, response.text
