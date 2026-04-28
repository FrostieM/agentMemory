from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.repositories.workspace_manifest_repo import (
    WorkspaceManifestError,
    ensure_workspace_manifest,
    get_workspace_manifest,
)


def _episode(workspace_id: str) -> EpisodeIn:
    return EpisodeIn(
        workspace_id=workspace_id,
        source_type=EpisodeSource.AGENT_ACTION,
        raw_text="workspace manifest control token",
        trust_level=TrustLevel.AGENT_OBSERVED,
    )


def test_workspace_manifest_created_for_fresh_db(applied_conn: sqlite3.Connection) -> None:
    manifest = ensure_workspace_manifest(
        applied_conn,
        workspace_id="project-a",
        allow_default_workspace=True,
    )

    assert manifest.workspace_id == "project-a"
    assert get_workspace_manifest(applied_conn) == manifest


def test_workspace_manifest_rejects_mismatched_existing_manifest(
    applied_conn: sqlite3.Connection,
) -> None:
    ensure_workspace_manifest(applied_conn, workspace_id="project-a", allow_default_workspace=True)

    with pytest.raises(WorkspaceManifestError, match="manifest mismatch"):
        ensure_workspace_manifest(
            applied_conn,
            workspace_id="project-b",
            allow_default_workspace=True,
        )


def test_workspace_manifest_rejects_foreign_non_default_rows(
    applied_conn: sqlite3.Connection,
) -> None:
    ingest_episode(applied_conn, _episode("project-b"))

    with pytest.raises(WorkspaceManifestError, match="existing rows"):
        ensure_workspace_manifest(
            applied_conn,
            workspace_id="project-a",
            allow_default_workspace=True,
        )


def test_workspace_manifest_rejects_default_when_project_mode(
    applied_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(WorkspaceManifestError, match="default"):
        ensure_workspace_manifest(
            applied_conn,
            workspace_id="default",
            allow_default_workspace=False,
        )
