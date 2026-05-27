from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_removed_legacy_routes_are_not_mounted_on_full_app(client: TestClient) -> None:
    post_routes = [
        "/memory/write_theory",
        "/memory/list_theories",
        "/memory/list_research_agenda",
        "/memory/list_concepts",
        "/memory/list_insights",
        "/memory/add_theory_evidence",
        "/memory/link_capability",
        "/memory/capability/record_outcome",
        "/memory/list_capability_links",
        "/memory/list_candidates",
        "/memory/list_audit",
        "/memory/list_disputes",
        "/memory/list_maintenance_events",
        "/memory/list_active_edits",
        "/memory/list_file_digests",
        "/memory/file_digest",
        "/memory/claim_edit",
        "/memory/release_edit",
        "/memory/rollback",
        "/memory/get_context",
        "/memory/write_decision",
        "/memory/decision_candidates",
        "/memory/insight_candidates",
        "/memory/snapshot_save",
        "/memory/snapshot_list",
        "/memory/snapshot_diff",
        "/memory/find_symbols",
        "/memory/graph_neighbors",
        "/memory/symbol_history",
        "/memory/breaking_changes",
        "/memory/soft_neighbors",
    ]
    for route in post_routes:
        assert client.post(route, json={"workspace_id": "default"}).status_code == 404

    for route in (
        "/memory/list",
        "/memory/count",
        "/memory/versions",
        "/memory/code_overview",
        "/memory/code_graph",
    ):
        assert client.get(route, params={"workspace_id": "default"}).status_code == 404

    for route in ("/ui/code", "/ui/graph"):
        assert client.get(route, params={"workspace_id": "default"}).status_code == 404
