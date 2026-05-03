"""Post-build hook gating tests for ``context_post_build.py``.

Locks the workspace-routing contracts:
* Project mode → scheduler fires only for own-workspace reads.
* Hub mode    → scheduler fires for any workspace, using the
  request-scoped DB path (read back from the connection via
  ``PRAGMA database_list``) rather than the singleton ``settings.db_path``.

The tests use a shim that records what ``maybe_run_sentinels`` would
receive — they don't actually spawn daemons. Workspace_id /
``last_sentinel_run_at`` writes are validated through the real
workspace_meta path in the dedicated scheduler tests.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_memory_lite.api.routes import context_post_build
from agent_memory_lite.config.settings import Settings


@pytest.fixture
def captured_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Replace maybe_run_sentinels with a recorder so we can assert on
    the routing decisions without spawning real daemons."""
    captured: list[dict] = []

    def fake_run(*, workspace_id, settings, db_path, embedding_provider, vector_store):
        captured.append(
            {
                "workspace_id": workspace_id,
                "db_path": str(db_path),
                "hub_mode": settings.hub_mode,
            }
        )
        return True

    monkeypatch.setattr(context_post_build, "maybe_run_sentinels", fake_run)
    return captured


def _settings(*, hub_mode: bool, workspace: str = "anchor") -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_HUB_MODE="true" if hub_mode else "false",
        MEMORY_WORKSPACE_ID=workspace,
        MEMORY_SENTINEL_AUTORUN_HOURS="6.0",
    )


def test_project_mode_skips_foreign_workspace(
    applied_conn: sqlite3.Connection, captured_calls: list[dict]
) -> None:
    """In project mode, scheduler only fires for the singleton workspace_id."""
    context_post_build.maybe_schedule_sentinels(
        applied_conn,
        settings=_settings(hub_mode=False, workspace="anchor"),
        request_workspace_id="other_project",
        embedding_provider=None,
        vector_store=None,
    )
    assert captured_calls == [], "scheduler fired for foreign workspace in project mode"


def test_project_mode_fires_for_own_workspace(
    applied_conn: sqlite3.Connection, captured_calls: list[dict]
) -> None:
    context_post_build.maybe_schedule_sentinels(
        applied_conn,
        settings=_settings(hub_mode=False, workspace="anchor"),
        request_workspace_id="anchor",
        embedding_provider=None,
        vector_store=None,
    )
    assert len(captured_calls) == 1
    assert captured_calls[0]["workspace_id"] == "anchor"


def test_hub_mode_fires_for_any_workspace(
    applied_conn: sqlite3.Connection, captured_calls: list[dict]
) -> None:
    """In hub mode, the strict-isolation gate is off — every workspace
    served by the hub gets its own scheduler tick."""
    context_post_build.maybe_schedule_sentinels(
        applied_conn,
        settings=_settings(hub_mode=True, workspace="anchor"),
        request_workspace_id="copyBot",
        embedding_provider=None,
        vector_store=None,
    )
    assert len(captured_calls) == 1
    assert captured_calls[0]["workspace_id"] == "copyBot"


def test_hub_mode_resolves_db_path_from_connection(
    applied_conn: sqlite3.Connection,
    tmp_db_path: Path,
    captured_calls: list[dict],
) -> None:
    """The path passed into the runner must come from the request-scoped
    connection (PRAGMA database_list), not the singleton settings.db_path
    — so cross-workspace hub reads write to the right workspace_meta."""
    context_post_build.maybe_schedule_sentinels(
        applied_conn,
        settings=_settings(hub_mode=True),
        request_workspace_id="copyBot",
        embedding_provider=None,
        vector_store=None,
    )
    assert len(captured_calls) == 1
    # applied_conn is opened on tmp_db_path; PRAGMA must report it back.
    captured_path = Path(captured_calls[0]["db_path"]).resolve()
    expected_path = tmp_db_path.resolve()
    assert captured_path == expected_path, (
        f"hub-mode scheduler used singleton db_path instead of request-scoped one: "
        f"got {captured_path}, expected {expected_path}"
    )
