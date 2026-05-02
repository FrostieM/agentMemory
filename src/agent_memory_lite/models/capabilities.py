"""Agent capability domain models.

These objects keep execution knowledge explicit: roles define responsibility
boundaries, skills define reusable procedures, and playbooks define repeatable
workflows. They are rendered into context alongside research memory.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentRoleIn(BaseModel):
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


class AgentRole(AgentRoleIn):
    id: str
    created_at: str
    updated_at: str


class AgentSkillIn(BaseModel):
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


class AgentSkill(AgentSkillIn):
    id: str
    created_at: str
    updated_at: str


class AgentPlaybookIn(BaseModel):
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


class AgentPlaybook(AgentPlaybookIn):
    id: str
    created_at: str
    updated_at: str


class AgentCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[AgentRole] = Field(default_factory=list)
    skills: list[AgentSkill] = Field(default_factory=list)
    playbooks: list[AgentPlaybook] = Field(default_factory=list)
