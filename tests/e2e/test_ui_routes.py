from __future__ import annotations

from fastapi.testclient import TestClient

from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn


def test_ui_index_and_assets(app_factory) -> None:
    app = app_factory()
    with TestClient(app) as client:
        index = client.get("/ui")
        js = client.get("/ui/app.js")
        css = client.get("/ui/styles.css")

    assert index.status_code == 200
    assert "Memory graph cockpit" in index.text
    assert js.status_code == 200
    assert "renderGraph" in js.text
    assert css.status_code == 200
    assert ".graph-panel" in css.text


def test_ui_state_returns_graph(
    app_factory, applied_conn, fake_embedding_provider, fake_vector_store
) -> None:
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
    assert body["recent"]
    assert body["signature"]


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
