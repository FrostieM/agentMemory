"""The immutable registry record for one hub-mode workspace.

Split out of ``workspace_registry`` so each module stays at or below the
150-SLOC ceiling (one concern per module). Re-exported from
``workspace_registry`` for back-compat with existing importers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkspaceEntry:
    id: str
    db_path: str
    vector_path: str
    label: str = ""
    project_root: str = ""
    registered_at: str = ""
    last_seen_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
