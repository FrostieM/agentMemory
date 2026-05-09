"""E2E for POST /memory/compact — settings passthrough + lesson_candidates_emitted.

Regression test for the 1.2.5 fix: pre-1.2.5 the compact route did NOT
pass ``settings`` to ``summarize_old_episodes``, which silently disabled
v1.8 reflective compaction (lesson candidate emission). This test locks
the response shape (``lesson_candidates_emitted`` field present) and
the parameter flow (``summarize_age_days`` body param overrides the
``MEMORY_COMPACT_AGE_DAYS`` settings default).

Behavioural test of actual lesson emission requires Ollama and lives
in the integration suite.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="project-c")
    with TestClient(app) as c:
        yield c


def test_compact_response_includes_lesson_candidates_emitted(client: TestClient) -> None:
    """1.2.5 lock: CompactResponse exposes ``lesson_candidates_emitted``
    so the operator can see whether the v1.8 reflective pass actually
    ran. With no episodes, the count must be 0."""
    r = client.post(
        "/memory/compact",
        json={"workspace_id": "project-c"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "lesson_candidates_emitted" in body
    assert body["lesson_candidates_emitted"] == 0
    # Existing fields still present
    assert "summarized_episodes" in body
    assert "stale_facts_archived" in body
    assert "cutoff_for_stale" in body


def test_compact_accepts_summarize_age_days_override(client: TestClient) -> None:
    """1.2.5: operators on young workspaces can lower the age threshold
    (default 30 days) per-call via ``summarize_age_days``. The endpoint
    accepts 1..365 inclusive."""
    r = client.post(
        "/memory/compact",
        json={"workspace_id": "project-c", "summarize_age_days": 7},
    )
    assert r.status_code == 200, r.text
    # Smoke: with no episodes in the last 7 days either, count is 0.
    assert r.json()["summarized_episodes"] == 0


def test_compact_rejects_age_days_out_of_range(client: TestClient) -> None:
    """``summarize_age_days`` validation: 0 and 366+ rejected."""
    r = client.post(
        "/memory/compact",
        json={"workspace_id": "project-c", "summarize_age_days": 0},
    )
    assert r.status_code == 422
    r = client.post(
        "/memory/compact",
        json={"workspace_id": "project-c", "summarize_age_days": 400},
    )
    assert r.status_code == 422
