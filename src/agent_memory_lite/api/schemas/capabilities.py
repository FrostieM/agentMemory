"""Wire-side schemas for agent capability memory."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.schemas._text_guard import SafeText


class UpsertAgentRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    name: SafeText = Field(min_length=1)
    purpose: SafeText = Field(min_length=1)
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
    name: SafeText = Field(min_length=1)
    summary: SafeText = Field(min_length=1)
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
    name: SafeText = Field(min_length=1)
    goal: SafeText = Field(min_length=1)
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
    # When true and the v1.5 maturity flag is on, every returned capability
    # gets its usage_count incremented and last_invoked_at refreshed. The
    # caller is committing to "I am about to use these"; the agent passes
    # this only when it is genuinely planning to invoke the capability,
    # not when it is just browsing.
    mark_used: bool = False


class RecordCapabilityOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    kind: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    success: bool
    episode_id: str | None = None


class RecordCapabilityOutcomeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str
    kind: str
    updated: bool
    success_count: int
    failure_count: int
    usage_count: int


class ListAgentCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[AgentRoleResponse] = Field(default_factory=list)
    skills: list[AgentSkillResponse] = Field(default_factory=list)
    playbooks: list[AgentPlaybookResponse] = Field(default_factory=list)
