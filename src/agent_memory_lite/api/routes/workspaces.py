"""GET/POST/DELETE /memory/workspaces - hub-mode workspace registry surface.

The endpoints are read-mostly. They never touch SQLite or LanceDB; they only
manage the JSON registry that lets a single HTTP service route requests to
multiple per-project memory files via `X-Memory-DB-Path`.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import SettingsDep
from agent_memory_lite.api.errors import ValidationError
from agent_memory_lite.api.schemas.workspaces import (
    WorkspaceDeleteResponse,
    WorkspaceEntryDTO,
    WorkspaceListResponse,
    WorkspaceRegisterRequest,
    WorkspaceRegisterResponse,
)
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_registry import (
    WorkspaceEntry,
    WorkspaceRegistry,
)

router = APIRouter()


def _registry(settings: Settings) -> WorkspaceRegistry:
    return WorkspaceRegistry(settings.workspaces_file)


def _to_dto(entry: WorkspaceEntry, current_workspace_id: str) -> WorkspaceEntryDTO:
    return WorkspaceEntryDTO(
        id=entry.id,
        db_path=entry.db_path,
        vector_path=entry.vector_path,
        label=entry.label,
        project_root=entry.project_root,
        registered_at=entry.registered_at,
        last_seen_at=entry.last_seen_at,
        is_current=entry.id == current_workspace_id,
        extra=dict(entry.extra),
    )


def _current_entry(settings: Settings) -> WorkspaceEntry:
    return WorkspaceEntry(
        id=settings.workspace_id,
        db_path=str(settings.db_path),
        vector_path=str(settings.vector_db_path),
        label=settings.workspace_id,
        project_root="",
        registered_at="",
        last_seen_at="",
    )


@router.get("/memory/workspaces", response_model=WorkspaceListResponse)
def list_workspaces(settings: SettingsDep) -> WorkspaceListResponse:
    registry = _registry(settings)
    entries = registry.list()
    seen_ids = {entry.id for entry in entries}
    current_id = settings.workspace_id
    if current_id and current_id not in seen_ids:
        entries.append(_current_entry(settings))
    entries.sort(key=lambda item: item.id)
    return WorkspaceListResponse(
        hub_mode=settings.hub_mode,
        current_workspace_id=current_id,
        workspaces=[_to_dto(entry, current_id) for entry in entries],
        registry_path=str(registry.path),
    )


@router.post("/memory/workspaces", response_model=WorkspaceRegisterResponse)
def register_workspace(
    body: WorkspaceRegisterRequest,
    settings: SettingsDep,
) -> WorkspaceRegisterResponse:
    if body.workspace_id == "default" and settings.forbid_default_workspace:
        raise ValidationError(
            "workspace_id='default' is disabled by MEMORY_FORBID_DEFAULT_WORKSPACE; "
            "register a project-specific workspace_id instead"
        )
    registry = _registry(settings)
    entry = registry.register(
        workspace_id=body.workspace_id,
        db_path=body.db_path,
        vector_path=body.vector_path,
        label=body.label,
        project_root=body.project_root,
        extra=body.extra,
    )
    return WorkspaceRegisterResponse(workspace=_to_dto(entry, settings.workspace_id))


@router.delete("/memory/workspaces/{workspace_id}", response_model=WorkspaceDeleteResponse)
def delete_workspace(
    workspace_id: str,
    settings: SettingsDep,
) -> WorkspaceDeleteResponse:
    registry = _registry(settings)
    removed = registry.remove(workspace_id)
    return WorkspaceDeleteResponse(workspace_id=workspace_id, removed=removed)
