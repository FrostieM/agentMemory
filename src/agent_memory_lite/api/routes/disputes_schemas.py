"""Wire schemas + DTO mapping for the v3.3 Vector 6 disputes routes.

Pydantic request/response models and the ``Dispute`` -> ``DisputeDTO``
projection helper, split out of ``disputes.py`` to keep the route
module focused on HTTP handlers. Re-exported from ``disputes`` so the
original import surface is unchanged.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.repositories.disputes_repo import Dispute


class DisputeDTO(BaseModel):
    """Wire-side projection of a dispute row."""

    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    target_kind: str
    target_id: str
    claimant_agent_id: str
    claim_text: str
    evidence: list[object] = Field(default_factory=list)
    status: str
    resolution: str = ""
    created_at: str
    resolved_at: str = ""


def _to_dto(d: Dispute) -> DisputeDTO:
    return DisputeDTO(
        id=d.id,
        workspace_id=d.workspace_id,
        target_kind=d.target_kind,
        target_id=d.target_id,
        claimant_agent_id=d.claimant_agent_id,
        claim_text=d.claim_text,
        evidence=list(d.evidence),
        status=d.status,
        resolution=d.resolution,
        created_at=d.created_at,
        resolved_at=d.resolved_at,
    )


class ProposeDisputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    target_kind: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    claimant_agent_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    evidence: list[object] = Field(default_factory=list)


class ProposeDisputeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispute: DisputeDTO


class ResolveDisputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispute_id: str = Field(min_length=1)
    status: str = Field(min_length=1)  # accepted | rejected | withdrawn
    resolution: str = ""


class ResolveDisputeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispute: DisputeDTO
    flipped: bool


class ListDisputesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    disputes: list[DisputeDTO]
