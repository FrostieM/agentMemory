"""Wire schemas for research taxonomy (concepts + insights).

Split out of ``research.py`` so the schema file stays under the SLOC
ceiling. Concepts hold reusable vocabulary entries; insights record
distilled lessons or open questions linked to theories / experiments.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_memory_lite.models.enums import ConceptKind, InsightStatus, InsightType


class UpsertConceptRequest(BaseModel):
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


class ConceptResponse(UpsertConceptRequest):
    concept_id: str
    created_at: str
    updated_at: str


class DistillInsightRequest(BaseModel):
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


class InsightResponse(DistillInsightRequest):
    insight_id: str
    created_at: str
    updated_at: str


class UpdateInsightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    insight_id: str = Field(min_length=1)
    target_type: str | None = Field(default=None, min_length=1)
    target_id: str | None = Field(default=None, min_length=1)
    status: InsightStatus | None = None
    source_episode_id: str | None = None

    @model_validator(mode="after")
    def require_update(self) -> UpdateInsightRequest:
        has_target_type = bool(self.target_type)
        has_target_id = bool(self.target_id)
        if has_target_type != has_target_id:
            raise ValueError("target_type and target_id must be provided together")
        if not has_target_type and self.status is None:
            raise ValueError("provide a target link or status update")
        return self


class ListConceptsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    include_inactive: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class ListConceptsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concepts: list[ConceptResponse]


class ListInsightsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    statuses: list[InsightStatus] | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ListInsightsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insights: list[InsightResponse]
