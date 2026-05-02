"""Wire schemas for POST /memory/pin."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PinRequest(BaseModel):
    workspace_id: str = "default"
    kind: str = Field(
        ...,
        description=("Object kind. Supported: decision, behavior_instruction, core_memory."),
    )
    id: str = Field(..., min_length=1)
    pinned: bool = True


class PinResponse(BaseModel):
    kind: str
    id: str
    pinned: bool
    found: bool
