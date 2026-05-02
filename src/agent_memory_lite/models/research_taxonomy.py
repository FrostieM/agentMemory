"""Research taxonomy domain models (concepts + insights).

Split out of ``research.py`` so the model file stays under the SLOC
ceiling. ``research.py`` re-exports these names so existing imports
keep working.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_memory_lite.models.enums import ConceptKind, InsightStatus, InsightType


class DomainConceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    name: str = Field(min_length=1)
    kind: ConceptKind = ConceptKind.TERM
    definition: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_episode_id: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    active: bool = True


class DomainConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    name: str
    kind: ConceptKind
    definition: str
    aliases: list[str]
    tags: list[str]
    source_episode_id: str | None
    confidence: float
    active: bool
    created_at: str
    updated_at: str


class ResearchInsightIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    insight_type: InsightType
    summary: str = Field(min_length=1)
    proposed_action: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    source_episode_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    status: InsightStatus = InsightStatus.NEW
    tags: list[str] = Field(default_factory=list)


class ResearchInsightUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    insight_id: str = Field(min_length=1)
    target_type: str | None = Field(default=None, min_length=1)
    target_id: str | None = Field(default=None, min_length=1)
    status: InsightStatus | None = None
    source_episode_id: str | None = None

    @model_validator(mode="after")
    def require_update(self) -> ResearchInsightUpdateIn:
        has_target_type = bool(self.target_type)
        has_target_id = bool(self.target_id)
        if has_target_type != has_target_id:
            raise ValueError("target_type and target_id must be provided together")
        if not has_target_type and self.status is None:
            raise ValueError("provide a target link or status update")
        return self


class ResearchInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    insight_type: InsightType
    summary: str
    proposed_action: str | None
    target_type: str | None
    target_id: str | None
    source_episode_ids: list[str]
    confidence: float
    status: InsightStatus
    tags: list[str]
    created_at: str
    updated_at: str
