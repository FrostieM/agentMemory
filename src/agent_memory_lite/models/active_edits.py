"""1.7.0: active-edit registry — short-lived claims by an agent that
it's currently editing a specific symbol or file. Other agents read
this before starting work to coordinate. TTL-bounded so a crashed
agent doesn't lock a symbol forever.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActiveEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    qualified_name: str | None = Field(default=None, max_length=400)
    file_path: str | None = Field(default=None, max_length=400)
    agent_id: str = Field(min_length=1, max_length=64)
    ttl_minutes: int = Field(default=30, ge=1, le=1440)
    note: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActiveEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    qualified_name: str | None
    file_path: str | None
    agent_id: str
    claimed_at: str
    expires_at: str
    note: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
