"""Wire-side schemas for agent capability memory."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UpsertAgentRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    responsibilities: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    handoff_triggers: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    source_episode_id: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    active: bool = True


class AgentRoleResponse(UpsertAgentRoleRequest):
    role_id: str
    created_at: str
    updated_at: str


class UpsertAgentSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    when_to_use: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    related_roles: list[str] = Field(default_factory=list)
    source_episode_id: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    active: bool = True


class AgentSkillResponse(UpsertAgentSkillRequest):
    skill_id: str
    created_at: str
    updated_at: str


class UpsertAgentPlaybookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    triggers: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    source_episode_id: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    active: bool = True


class AgentPlaybookResponse(UpsertAgentPlaybookRequest):
    playbook_id: str
    created_at: str
    updated_at: str


class ListAgentCapabilitiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    include_inactive: bool = False
    limit: int = Field(default=6, ge=1, le=50)


class ListAgentCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[AgentRoleResponse] = Field(default_factory=list)
    skills: list[AgentSkillResponse] = Field(default_factory=list)
    playbooks: list[AgentPlaybookResponse] = Field(default_factory=list)
