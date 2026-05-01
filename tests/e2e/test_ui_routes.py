from __future__ import annotations

from fastapi.testclient import TestClient

from agent_memory_lite.api.ui_telemetry import UiTelemetryBus, ui_telemetry
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.maintenance.usage_feedback import record_usage_feedback
from agent_memory_lite.models.enums import (
    EpisodeSource,
    MaintenanceEventStatus,
    MaintenanceSeverity,
    TrustLevel,
)
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.repositories.maintenance_repo import insert_maintenance_event_row
from agent_memory_lite.utils.time import iso_now


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
    assert "ResizeObserver" in js.text
    assert "graphZoomIn" in js.text
    assert "graphTestBtn" in index.text
    assert "startGraphDemo" in js.text
    assert "demoRoutesForRequest" in js.text
    assert '["skills", 2]' in js.text
    assert '["decisions", 3]' in js.text
    assert "continuousRoutePath" in js.text
    assert "route-link" in js.text
    assert "animationQueue" in js.text
    assert "startNextGraphJob" in js.text
    assert "flushPendingGraphJob" in js.text
    assert "lastGraphRoutes" in js.text
    assert "retireLastRoutesBeforeNextJob" in js.text
    assert "priority = false" in js.text
    assert 'source !== "demo"' in js.text
    assert "shouldAnimateGraphEvent" in js.text
    assert "uiStartedAtMs" in js.text
    assert "graphAnimationInProgress" in js.text
    assert "renderGraphRouteLayer" in js.text
    assert "clearGraphRouteLayer" in js.text
    assert "graph-route-layer" in js.text
    assert "graph-object-layer" in js.text
    assert "ensureGraphLayer" in js.text
    assert "state.memory?.graph && !graphAnimationInProgress()" in js.text
    assert "routesFromActiveRequestEvents" in js.text
    assert "latestByHub" not in js.text
    assert "slice(0, 18)" in js.text
    assert "stageBadge" in js.text
    assert "steady-path" in js.text
    assert "graphZoom: 0.82" in js.text
    assert "setGraphZoom(0.82)" in js.text
    assert "width * 0.1" in js.text
    assert "objectReserve" in js.text
    assert "maxHubRadiusX" in js.text
    assert "retiringRoutes" in js.text
    assert "routeFromGraphDelta" in js.text
    assert "prioritizeGraphRoutes" in js.text
    assert "routeVisualPriority" in js.text
    assert "agent_roles: 0" in js.text
    assert "agent_skills: 1" in js.text
    assert "agent_playbooks: 2" in js.text
    assert "behavior_instructions: 3" in js.text
    assert "updateLiveGraphFromEvents" in js.text
    assert "!routes.length && state.lastGraphRoutes.length" in js.text
    assert "activeObjectPositions.forEach" in js.text
    assert "stableHash" in js.text
    assert "hashUnit" in js.text
    assert "routeAgeFactor" in js.text
    assert "ageFactor * 260" in js.text
    assert "buildRouteObjectLayout" in js.text
    assert "lineCircleDistance" in js.text
    assert "pushRoutePointAway" in js.text
    assert "20260501-hub-workspace-switch" in index.text
    assert "graphClickCandidate" in js.text
    assert "moved <= 10" in js.text
    assert "!state.sseReady && !state.paused" in js.text
    assert "setGraphZoom(0.56)" not in js.text
    assert "semantic-root" in js.text
    assert "semantic-object-id" in js.text
    assert "renderLiveGraph" not in js.text
    assert '<select id="workspaceInput"' in index.text
    assert "warningsPanel" in index.text
    assert "graphInspector" in index.text
    assert "showWorkspaceInspector" in js.text
    assert "showHubInspectorById" in js.text
    assert "showGraphNodeInspectorFromElement" in js.text
    assert "graphInteractiveNode(event.target)" in js.text
    assert 'els.graph?.addEventListener("click"' in js.text
    assert "inspector-card" in js.text
    assert "aria-modal" in js.text
    assert 'body.classList.add("inspector-open")' in js.text
    assert "event.target === els.graphInspector" in js.text
    assert 'event.key === "Escape"' in js.text
    assert "graphSummary" not in index.text
    assert "Query uses" not in js.text
    assert "Context route" not in js.text
    assert "Live path" not in index.text
    assert "liveGraphSvg" not in index.text
    assert "stageRail" not in index.text
    assert css.status_code == 200
    assert ".live-stage" not in css.text
    assert "top: 1rem" in css.text
    assert "max-height: calc(100vh - 2rem)" in css.text
    assert "width: 1456px" not in css.text
    assert "height: 952px" not in css.text
    assert ".semantic-object-label" in css.text
    assert ".semantic-object-id" in css.text
    assert ".inspector-card" in css.text
    assert "position: fixed" in css.text
    assert "place-items: center" in css.text
    assert "body.inspector-open" in css.text
    assert "routeForward" in css.text
    assert "objectPoofIn" in css.text
    assert "objectPopOut" in css.text
    assert "display: none" in css.text
    assert ".memory-route" in css.text


def test_ui_state_returns_graph(
    app_factory, applied_conn, fake_embedding_provider, fake_vector_store
) -> None:
    ui_telemetry.clear()
    result = ingest_episode(
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
    record_usage_feedback(
        applied_conn,
        workspace_id="default",
        source_type="chunk",
        source_id=result.chunk.id,
        query="ui graph smoke",
        usefulness=0.75,
        notes="graph dependency test",
    )
    insert_maintenance_event_row(
        applied_conn,
        event_id="me_ui_warning",
        workspace_id="default",
        kind="retrieval_integrity",
        severity=MaintenanceSeverity.WARNING,
        status=MaintenanceEventStatus.OPEN,
        summary="UI warning smoke event",
        details={"check": "smoke"},
        source_episode_id=None,
        target_type="chunks",
        target_id=result.chunk.id,
        created_at=iso_now(),
    )
    applied_conn.commit()

    app = app_factory()
    with TestClient(app) as client:
        response = client.get("/memory/ui/state?workspace_id=default")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "default" in body["workspaces"]
    assert body["warnings"]
    assert body["warnings"][0]["summary"] == "UI warning smoke event"
    assert body["counts"]["episodes"] >= 1
    assert body["counts"]["chunks"] >= 1
    assert body["graph"]["nodes"]
    assert body["graph"]["edges"]
    edge_pairs = {
        (edge["source"], edge["target"], edge["label"]) for edge in body["graph"]["edges"]
    }
    assert (
        f"chunks:{result.chunk.id}",
        next(
            node["id"] for node in body["graph"]["nodes"] if node["kind"] == "memory_usage_feedback"
        ),
        "rates",
    ) in edge_pairs
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
        explain = client.post(
            "/memory/explain_context",
            json={
                "workspace_id": "default",
                "query": "telemetry search smoke",
                "max_tokens": 2500,
            },
        )
        state = client.get("/memory/ui/state?workspace_id=default")
        stream = client.get("/memory/ui/events?workspace_id=default&once=true")

    assert search.status_code == 200
    assert explain.status_code == 200
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: memory" in stream.text
    state_body = state.json()
    labels = [event["label"] for event in state_body["latest_events"]]
    stages = [event["stage"] for event in state_body["latest_events"]]
    used_graph_events = [
        event
        for event in state_body["latest_events"]
        if event["type"] == "graph_delta" and event["counts"].get("action") == "used"
    ]
    assert "Search query accepted" in labels
    assert "fts" in stages
    assert used_graph_events
    assert any(event["stage"] == "context" for event in used_graph_events)
    assert any(event["counts"].get("object_type") == "chunk" for event in used_graph_events)
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
