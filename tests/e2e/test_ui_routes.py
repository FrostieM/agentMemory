from __future__ import annotations

from fastapi.testclient import TestClient

from agent_memory_lite.api.ui_telemetry import UiTelemetryBus, ui_telemetry
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn


def test_ui_telemetry_bus_is_bounded_and_redacts() -> None:
    bus = UiTelemetryBus(max_events=2)
    for index in range(3):
        bus.record(
            workspace_id="default",
            event_type="stage_done",
            endpoint="/memory/search",
            operation="search",
            stage="fts",
            label=f"event {index}",
            status="ok",
            snippet="token sk-proj-abcdefghijklmnopqrstuvwx",
        )

    events = bus.snapshot(workspace_id="default", limit=10)
    assert len(events) == 2
    assert "event 0" not in {event["label"] for event in events}
    assert "<<REDACTED:OPENAI_KEY>>" in events[-1]["snippet"]


def test_ui_index_and_assets(app_factory) -> None:
    app = app_factory()
    with TestClient(app) as client:
        index = client.get("/ui")
        js = client.get("/ui/app.js")
        css = client.get("/ui/styles.css")

    assert index.status_code == 200
    assert "Live memory flow" in index.text
    assert js.status_code == 200
    assert "EventSource" in js.text
    assert "renderProcess" in js.text
    assert css.status_code == 200
    assert ".live-stage" in css.text


def test_ui_state_returns_graph(
    app_factory, applied_conn, fake_embedding_provider, fake_vector_store
) -> None:
    ui_telemetry.clear()
    ingest_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="ui graph smoke episode with retrieval edge",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    app = app_factory()
    with TestClient(app) as client:
        response = client.get("/memory/ui/state?workspace_id=default")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["counts"]["episodes"] >= 1
    assert body["counts"]["chunks"] >= 1
    assert body["graph"]["nodes"]
    assert body["graph"]["edges"]
    assert body["process"]["stages"]
    assert body["process"]["edges"]
    assert body["process"]["events"]
    assert "latest_events" in body
    assert "graph_deltas" in body
    assert "active_requests" in body
    assert body["recent"]
    assert body["signature"]


def test_ui_events_stream_and_search_telemetry(
    app_factory, applied_conn, fake_embedding_provider, fake_vector_store
) -> None:
    ui_telemetry.clear()
    ingest_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="telemetry search smoke token for live observatory",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    app = app_factory()
    with TestClient(app) as client:
        search = client.post(
            "/memory/search",
            json={
                "workspace_id": "default",
                "query": "telemetry search smoke",
                "mode": "fts",
                "limit": 5,
            },
        )
        state = client.get("/memory/ui/state?workspace_id=default")
        stream = client.get("/memory/ui/events?workspace_id=default&once=true")

    assert search.status_code == 200
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: memory" in stream.text
    state_body = state.json()
    labels = [event["label"] for event in state_body["latest_events"]]
    stages = [event["stage"] for event in state_body["latest_events"]]
    assert "Search query accepted" in labels
    assert "fts" in stages
    assert state_body["active_requests"] == []


def test_ui_state_uses_configured_workspace_under_strict_guard(app_factory) -> None:
    app = app_factory(
        MEMORY_WORKSPACE_ID="project-a",
        MEMORY_STRICT_WORKSPACE_ISOLATION="true",
    )
    with TestClient(app) as client:
        ok = client.get("/memory/ui/state")
        blocked = client.get("/memory/ui/state?workspace_id=project-b")

    assert ok.status_code == 200
    assert ok.json()["workspace_id"] == "project-a"
    assert blocked.status_code == 400
    assert "MEMORY_STRICT_WORKSPACE_ISOLATION" in str(blocked.json())
