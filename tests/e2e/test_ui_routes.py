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
    """The /ui endpoint serves the Memory · Live Observatory shell.

    The redesigned UI ships three assets: index.html (skeleton), app.js
    (vanilla JS observatory), and styles.css. The asserts below check the
    public contract — wiring to the backend endpoints, key DOM hooks, and
    the visual identity of the design — without locking down internal
    function names so future refactors can land without ceremonious test
    rewrites.
    """
    app = app_factory()
    with TestClient(app) as client:
        index = client.get("/ui")
        js = client.get("/ui/app.js")
        css = client.get("/ui/styles.css")

    # --- index.html: structural elements + cache-bust + design title ---
    assert index.status_code == 200
    assert "Memory · Live Observatory" in index.text
    assert "20260501-observatory-rewrite" in index.text
    # Header chips and workspace dropdown.
    assert 'id="healthChip"' in index.text
    assert 'id="chunksChip"' in index.text
    assert 'id="workspaceInput"' in index.text
    assert 'id="tokenInput"' in index.text
    # Graph pane and SVG canvas.
    assert 'id="graphSvg"' in index.text
    assert 'class="graph-pane"' in index.text
    assert 'class="graph-shell"' in index.text
    # Right rail cards (inspector, query, life trail).
    assert 'id="inspectorCard"' in index.text
    assert 'id="queryInput"' in index.text
    assert 'id="lifeFeed"' in index.text
    assert 'id="warningsPanel"' in index.text
    # Tweaks panel (bottom right).
    assert 'id="tweaksPanel"' in index.text
    assert 'id="hueRange"' in index.text
    assert 'id="speedRange"' in index.text
    assert 'id="densityInput"' in index.text
    # Pause / refresh controls.
    assert 'id="pauseBtn"' in index.text
    assert 'id="refreshBtn"' in index.text

    # --- app.js: backend wiring + design contract ---
    assert js.status_code == 200
    # Hits the real backend endpoints.
    assert "/memory/ui/state" in js.text
    assert "/memory/ui/events" in js.text
    assert "/memory/search" in js.text
    assert "/memory/get_context" in js.text
    # Uses SSE for live events.
    assert "EventSource" in js.text
    # Honors the asymmetric isolation contract: per-call X-Memory-DB-Path
    # routing for cross-workspace reads.
    assert "X-Memory-DB-Path" in js.text
    assert "X-Memory-Vector-Path" in js.text
    # Eight families from the design.
    assert '"episodes"' in js.text
    assert '"decisions"' in js.text
    assert '"research"' in js.text
    assert '"tasks"' in js.text
    assert '"roles"' in js.text
    assert '"skills"' in js.text
    assert '"instructions"' in js.text
    assert '"feedback"' in js.text
    # Renders the user-supplied label (migration 0015) when present.
    assert "short_label" in js.text
    # Polling fallback when SSE is offline.
    assert "REFRESH_MS" in js.text or "setInterval" in js.text
    # SSE-driven animation queue: events arrive as a sequence sharing one
    # request_id (request_started → graph_delta(s) → request_done). The
    # observatory groups them into ONE query per real operation — no
    # synthetic auto-rotation through recent rows. After a cycle, REVERSE
    # retracts visuals, the graph empties, and the next coalesced
    # operation is dequeued. If the service is silent, the graph stays
    # idle.
    assert "state.queue" in js.text
    assert "enqueueQuery" in js.text
    assert "startNextFromQueue" in js.text
    assert "requestBuffer" in js.text
    assert "flushRequest" in js.text
    assert "REQUEST_FLUSH_AFTER_MS" in js.text
    assert "request_started" in js.text
    assert "request_done" in js.text
    assert "graph_delta" in js.text
    assert "FAMILY_BY_OBJECT_TYPE" in js.text
    assert "IDLE_GAP_MS" in js.text
    assert "lastIntent" in js.text
    # Object body fields surface in the inspector.
    assert "decision_text" in js.text
    assert "predictions" in js.text
    assert "validation_criteria" in js.text
    # Click on a node = highlight only (no new edges).
    assert "is-highlight" in js.text

    # --- styles.css: design identity (oklch, dark theme, tweaks panel) ---
    assert css.status_code == 200
    assert "oklch" in css.text
    assert "tweaks-panel" in css.text
    assert ".rail-card" in css.text
    assert ".graph-pane" in css.text
    assert ".main-grid" in css.text
    assert ".chip" in css.text
    assert "--accent-hue" in css.text
    # Legacy markers from the old observatory must not survive.
    assert "memory-map-section" not in index.text
    assert "live-feed-panel" not in index.text
    assert "graphZoomIn" not in index.text


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


def test_ui_state_allows_cross_workspace_reads_under_strict_guard(app_factory) -> None:
    """Strict isolation blocks writes, not reads. UI inspection is a read."""
    app = app_factory(
        MEMORY_WORKSPACE_ID="project-a",
        MEMORY_STRICT_WORKSPACE_ISOLATION="true",
    )
    with TestClient(app) as client:
        own = client.get("/memory/ui/state")
        cross = client.get("/memory/ui/state?workspace_id=project-b")

    assert own.status_code == 200
    assert own.json()["workspace_id"] == "project-a"
    # Cross-workspace reads are allowed: the user can explicitly inspect
    # another project's memory from any chat. Writes are still blocked
    # (covered by separate write-route tests).
    assert cross.status_code == 200
    assert cross.json()["workspace_id"] == "project-b"
