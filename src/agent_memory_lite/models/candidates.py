"""Memory candidate produced by an extractor.

A candidate is a structured proposal to write something into memory. The
ingestion pipeline runs candidates through `extraction/trust_gate.py` and
`extraction/thresholds.py` before any of them turns into a row.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import MemoryCandidateKind, TrustLevel


class TemporalSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: str
    valid_from: str
    valid_to: str | None = None


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MemoryCandidateKind
    subject: str
    predicate: str
    object: str | None = None
    evidence: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    trust_level: TrustLevel = TrustLevel.UNKNOWN
    temporal: TemporalSpan
    write_targets: list[str] = Field(default_factory=list)
    source_episode_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
