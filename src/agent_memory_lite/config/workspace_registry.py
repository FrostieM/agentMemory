"""Hub-mode workspace registry.

Stores the set of (workspace_id, db_path, vector_path, project_root) entries
the local UI is allowed to switch between. The file is JSON, kept in
`~/.agent_memory/workspaces.json` by default and overridable via
`MEMORY_WORKSPACES_FILE`. The registry is purely a UI/router concern: it
does not own SQLite; the per-request `X-Memory-DB-Path` header is still the
authoritative isolation primitive.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent_memory_lite.utils.time import iso_now


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


class WorkspaceRegistry:
    """JSON-backed list of {workspace_id, db_path, vector_path}.

    Operations are atomic: every write loads, mutates, and rewrites the file
    under a process-wide lock. There is no expectation of cross-host sharing.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, dict):
            return []
        entries = raw.get("workspaces", [])
        return [entry for entry in entries if isinstance(entry, dict) and entry.get("id")]

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "workspaces": entries}
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def list(self) -> list[WorkspaceEntry]:
        with self._lock:
            return [
                WorkspaceEntry(
                    id=str(item["id"]),
                    db_path=str(item.get("db_path", "")),
                    vector_path=str(item.get("vector_path", "")),
                    label=str(item.get("label", "")),
                    project_root=str(item.get("project_root", "")),
                    registered_at=str(item.get("registered_at", "")),
                    last_seen_at=str(item.get("last_seen_at", "")),
                    extra=dict(item.get("extra", {})),
                )
                for item in self._load()
            ]

    def get(self, workspace_id: str) -> WorkspaceEntry | None:
        for entry in self.list():
            if entry.id == workspace_id:
                return entry
        return None

    def register(
        self,
        *,
        workspace_id: str,
        db_path: str,
        vector_path: str,
        label: str = "",
        project_root: str = "",
        extra: dict[str, Any] | None = None,
    ) -> WorkspaceEntry:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        if not db_path:
            raise ValueError("db_path is required")
        with self._lock:
            entries = self._load()
            now = iso_now()
            updated: dict[str, Any] | None = None
            registered_at = now
            for entry in entries:
                if entry.get("id") == workspace_id:
                    registered_at = str(entry.get("registered_at", now))
                    entry.update(
                        {
                            "db_path": db_path,
                            "vector_path": vector_path,
                            "label": label or entry.get("label", ""),
                            "project_root": project_root or entry.get("project_root", ""),
                            "registered_at": registered_at,
                            "last_seen_at": now,
                        }
                    )
                    if extra:
                        merged = dict(entry.get("extra", {}))
                        merged.update(extra)
                        entry["extra"] = merged
                    updated = entry
                    break
            if updated is None:
                updated = {
                    "id": workspace_id,
                    "db_path": db_path,
                    "vector_path": vector_path,
                    "label": label,
                    "project_root": project_root,
                    "registered_at": now,
                    "last_seen_at": now,
                    "extra": dict(extra or {}),
                }
                entries.append(updated)
            entries.sort(key=lambda item: str(item.get("id", "")))
            self._write(entries)
            return WorkspaceEntry(**{**updated, "extra": dict(updated.get("extra", {}))})

    def remove(self, workspace_id: str) -> bool:
        with self._lock:
            entries = self._load()
            kept = [item for item in entries if item.get("id") != workspace_id]
            if len(kept) == len(entries):
                return False
            self._write(kept)
            return True

    def touch(self, workspace_id: str) -> None:
        """Update the last_seen_at timestamp without rewriting other fields."""
        with self._lock:
            entries = self._load()
            for entry in entries:
                if entry.get("id") == workspace_id:
                    entry["last_seen_at"] = iso_now()
                    self._write(entries)
                    return
