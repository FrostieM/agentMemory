"""Request/response schemas for /memory/workspaces."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceEntryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    db_path: str
    vector_path: str
    label: str = ""
    project_root: str = ""
    registered_at: str = ""
    last_seen_at: str = ""
    is_current: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class WorkspaceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hub_mode: bool
    current_workspace_id: str
    workspaces: list[WorkspaceEntryDTO]
    registry_path: str


class WorkspaceRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    db_path: str = Field(min_length=1)
    vector_path: str = Field(min_length=1)
    label: str = ""
    project_root: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class WorkspaceRegisterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: WorkspaceEntryDTO


class WorkspaceDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    removed: bool
