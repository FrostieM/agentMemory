"""Wire-side suggestion payloads shared by write routes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CapabilitySuggestionPayload(BaseModel):
    """Server-ranked capability suggestion."""

    model_config = ConfigDict(extra="forbid")

    capability_type: str
    capability_id: str
    capability_name: str
    score: float
    snippet: str


class DecisionNeighborPayload(BaseModel):
    """Server-ranked existing-decision suggestion."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    title: str
    snippet: str
    score: float
    status: str
