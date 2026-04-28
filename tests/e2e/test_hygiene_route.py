from __future__ import annotations

from fastapi.testclient import TestClient


def test_hygiene_report_route(app_factory) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    with TestClient(app) as client:
        response = client.get("/memory/hygiene_report", params={"workspace_id": "project-a"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workspace_id"] == "project-a"
    assert body["status"] in {"ok", "warning"}
    assert isinstance(body["counts"], dict)
    assert isinstance(body["findings"], list)
