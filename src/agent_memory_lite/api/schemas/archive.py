"""Wire schemas for POST /memory/archive."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArchiveRequest(BaseModel):
    workspace_id: str = "default"
    kind: str = Field(
        ...,
        description=(
            "Object kind: chunk, episode, file, decision, theory, insight, "
            "role, skill, playbook, behavior_instruction, candidate."
        ),
    )
    id: str = Field(..., min_length=1)
    archive: bool = True


class ArchiveResponse(BaseModel):
    kind: str
    id: str
    archived: bool
    found: bool
