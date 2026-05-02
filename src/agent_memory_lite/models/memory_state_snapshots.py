"""Memory state snapshot domain types.

Distinct from ``models/research.py``'s ``MemorySnapshot`` (which is
a catalog entry for an external research dataset). A
``MemoryStateSnapshot`` is a point-in-time digest of the workspace's
own memory used by the snapshot/diff tooling.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MemoryStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    name: str
    taken_at: str
    counts: dict[str, int] = Field(default_factory=dict)
    digests: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class MemoryStateSnapshotDiff(BaseModel):
    """Diff between two state snapshots.

    The diff distinguishes id-set changes (added / removed) from
    content changes (same id, different short hash) so a reader can
    tell "this theory is new" apart from "this theory's claim
    changed".
    """

    model_config = ConfigDict(extra="forbid")

    before_snapshot_id: str
    after_snapshot_id: str
    before_taken_at: str
    after_taken_at: str
    counts_delta: dict[str, int] = Field(default_factory=dict)
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
