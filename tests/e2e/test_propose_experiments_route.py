"""End-to-end tests for /memory/propose_experiments (v3.1 Vector 1).

The e2e DB fixture applies the legacy ``migrations/*.sql`` set only,
which creates ``research_insights`` but NOT the canonical v3
``insights`` table (consolidation.py writes there in production where
both schemas coexist). These tests therefore exercise only the HTTP
plumbing on an empty workspace; the proposal-scanner semantics are
covered exhaustively by ``tests/unit/maintenance/test_experiment_proposal.py``
which sets up the hybrid schema directly.
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


def test_propose_experiments_get_empty_workspace(client: TestClient) -> None:
    """GET returns 200 + empty proposals on a fresh workspace.

    The e2e fixture is legacy-only — no canonical ``insights`` table —
    so the route reports ``available=false`` rather than crashing
    (Vector1-audit-2 M5)."""
    r = client.get("/memory/propose_experiments", params={"workspace_id": "default"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_id"] == "default"
    assert body["proposals"] == []
    assert body["persisted"] == 0
    assert body["candidate_ids"] == []
    assert body["available"] is False  # legacy-only fixture; insights table missing
    assert body["feature_enabled"] is True


def test_propose_experiments_post_empty_workspace(client: TestClient) -> None:
    """POST on empty workspace persists nothing, returns 200."""
    r = client.post("/memory/propose_experiments", params={"workspace_id": "default"}, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["persisted"] == 0
    assert body["available"] is False


def test_propose_experiments_rejects_invalid_limit(client: TestClient) -> None:
    """``limit`` is bounded ge=1 le=50 by FastAPI validation."""
    r = client.get(
        "/memory/propose_experiments",
        params={"workspace_id": "default", "limit": 0},
    )
    assert r.status_code == 422
    r2 = client.get(
        "/memory/propose_experiments",
        params={"workspace_id": "default", "limit": 100},
    )
    assert r2.status_code == 422


def test_propose_experiments_requires_workspace_id(client: TestClient) -> None:
    """``workspace_id`` is required (min_length=1)."""
    r = client.get("/memory/propose_experiments")
    assert r.status_code == 422


def test_propose_experiments_post_respects_disabled_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vector1-audit-2 M5: when the feature flag is off, POST returns
    empty (no writes) — same shape as no insights but explicitly safe."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_ENABLED", "false")
    r = client.post("/memory/propose_experiments", params={"workspace_id": "default"}, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proposals"] == []
    assert body["persisted"] == 0
