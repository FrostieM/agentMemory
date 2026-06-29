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
from pathlib import Path
from typing import Any

from agent_memory_lite.config.workspace_entry import WorkspaceEntry
from agent_memory_lite.utils.atomic_write import atomic_write_text, read_text_retrying
from agent_memory_lite.utils.time import iso_now

__all__ = ["WorkspaceEntry", "WorkspaceRegistry"]


class WorkspaceRegistry:
    """JSON-backed list of {workspace_id, db_path, vector_path}.

    Operations are atomic: every write loads, mutates, and rewrites the file
    under a process-wide lock. There is no expectation of cross-host sharing.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        # Reason the most recent _load() could not READ an EXISTING registry file;
        # None when the file was absent (a clean empty) or parsed successfully.
        self._last_load_error: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def last_load_error(self) -> str | None:
        """Corrupt-vs-absent signal from the most recent ``_load()``.

        ``'corrupt:json'`` / ``'corrupt:io'`` / ``'corrupt:shape'`` when an EXISTING
        file could not be parsed; ``None`` for a missing file or a clean parse. Lets
        routing callers fail-closed with "registry unavailable" instead of silently
        treating an unreadable registry as "no workspaces". Meaningful only right
        after a load (list/get/register/remove/touch).
        """
        return self._last_load_error

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            self._last_load_error = None  # absent is a clean empty, not an error
            return []
        try:
            raw = json.loads(read_text_retrying(self._path))
        except json.JSONDecodeError:
            self._last_load_error = "corrupt:json"
            return []
        except OSError:
            self._last_load_error = "corrupt:io"
            return []
        if not isinstance(raw, dict):
            self._last_load_error = "corrupt:shape"
            return []
        self._last_load_error = None  # a successful parse clears any prior error
        entries = raw.get("workspaces", [])
        return [entry for entry in entries if isinstance(entry, dict) and entry.get("id")]

    def _write(self, entries: list[dict[str, Any]]) -> None:
        # Atomic replace (temp file + os.replace): a concurrent reader -- e.g. the
        # read guard's registry_load_error() check, which opens the file unlocked --
        # sees either the old or the new COMPLETE file, never a torn/truncated parse
        # that would read as 'corrupt' and trigger a spurious workspace_unavailable.
        # Makes the class docstring's "Operations are atomic" true on the file too.
        payload = {"version": 1, "workspaces": entries}
        atomic_write_text(self._path, json.dumps(payload, indent=2, sort_keys=True))

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
