"""2.0: response models for /memory/code_overview.

Split from ``code_overview.py`` so the route handler stays under
the SLOC ceiling. Six small pydantic types describing the
dashboard payload.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CodeCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: int
    chunks: int
    symbols: int
    edges: int
    versions: int
    soft_edges: int


class RecentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str
    language: str | None
    symbol_count: int
    inbound_edge_count: int
    outbound_edge_count: int
    versions_recent: int
    narrative: str
    updated_at: str


class BreakingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qualified_name: str
    file_path: str | None
    prev_signature: str
    new_signature: str
    new_at: str


class ActiveEditItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qualified_name: str | None
    file_path: str | None
    agent_id: str
    expires_at: str
    note: str | None


class HotSymbol(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qualified_name: str
    inbound_calls: int


class CodeOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    counts: CodeCounts
    recent_files: list[RecentFile]
    breaking: list[BreakingItem]
    active_edits: list[ActiveEditItem]
    top_called: list[HotSymbol]
