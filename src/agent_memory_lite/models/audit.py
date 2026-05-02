"""Audit log entries.

Every write that mutates non-trivial state (decisions, facts, core memory) gets
an audit row. `before_json` is null on creation; `after_json` is null on deletion.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    action: str
    target_type: str
    target_id: str
    source_episode_id: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    created_at: str = Field(default="")
