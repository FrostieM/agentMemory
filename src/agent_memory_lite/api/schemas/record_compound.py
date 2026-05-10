"""Wire-side schemas for the Move 2 compound write tool.

`POST /memory/record_with_evidence` bundles three discipline-rule
follow-ups (ingest_episode → write_decision → link_capability) into one
atomic call so the agent doesn't have to remember each step. The
schemas live in their own module to keep the route file under SLOC.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_memory_lite.api.schemas._text_guard import SafeText, SafeTextOptional
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityType,
    EpisodeSource,
    TrustLevel,
)


class RecordWithEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"

    # The episode (evidence) — required side of the bundle. The agent's
    # observation that supports the decision; written first so the
    # decision can carry source_episode_id.
    evidence_text: SafeText = Field(min_length=1)
    evidence_trust_level: TrustLevel = TrustLevel.AGENT_OBSERVED
    evidence_importance: float = Field(default=0.6, ge=0.0, le=1.0)
    evidence_source_type: EpisodeSource = EpisodeSource.AGENT_ACTION
    session_id: str | None = None
    task_id: str | None = None

    # The decision — second step. source_episode_id is filled from
    # the episode this call just wrote; the explicit field exists in
    # standard write_decision but here it's owned by the bundle.
    decision_title: SafeText = Field(min_length=1)
    decision_text: SafeText = Field(min_length=1)
    decision_rationale: SafeTextOptional = None
    decision_importance: float = Field(default=0.8, ge=0.0, le=1.0)
    decision_confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    supersedes_decision_id: str | None = None
    references: list[str] = Field(default_factory=list)

    # Optional capability link — third step. All three of (type, name,
    # relation) must be provided together or all left empty. The model
    # validator below enforces that.
    capability_type: CapabilityType | None = None
    capability_name: str | None = None
    capability_relation: CapabilityLinkRelation | None = None
    capability_rationale: SafeTextOptional = None
    capability_strength: float = Field(default=0.7, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _capability_triplet(self) -> RecordWithEvidenceRequest:
        provided = [
            self.capability_type is not None,
            self.capability_name is not None,
            self.capability_relation is not None,
        ]
        if any(provided) and not all(provided):
            raise ValueError(
                "capability_type / capability_name / capability_relation must "
                "all be provided together (or all left empty for no link)."
            )
        return self


class RecordWithEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    episode_id: str
    decision_id: str
    decision_status: str
    valid_from: str
    superseded_decision_id: str | None
    capability_link_id: str | None
    chunk_id: str
