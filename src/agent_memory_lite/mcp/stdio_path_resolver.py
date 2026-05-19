"""CWD → workspace path resolution for the MCP stdio runtime.

Lifted out of ``stdio_runtime`` so that module stays under the
150-SLOC ceiling after the ``_open_lock`` addition (audit 1 #3).

Resolution order (each step short-circuits when satisfied):

1. ``MEMORY_DB_PATH`` env set → caller already pinned the DB; return
   ``settings`` unchanged.
2. ``./.agent_memory/memory.db`` exists relative to ``cwd`` → use it;
   enable hub mode when the registry has multiple workspaces.
3. Registry has exactly ONE entry → adopt it (single-project layout).
4. Registry has ≥2 entries AND strict isolation is off → adopt the
   first entry and enable hub mode.
5. Fallback: return ``settings`` unchanged (caller uses defaults).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry


def resolve_paths_from_cwd(settings: Settings) -> Settings:
    """Override ``settings.db_path`` / ``settings.vector_db_path`` from cwd."""
    import os  # noqa: PLC0415 — lazy so test-time env edits stay live

    if os.environ.get("MEMORY_DB_PATH"):
        return settings
    cwd = Path.cwd()
    candidate_db = cwd / ".agent_memory" / "memory.db"
    candidate_vec = cwd / ".agent_memory" / "vectors.lance"
    try:
        registry = WorkspaceRegistry(settings.workspaces_file)
        entries = registry.list()
    except Exception:
        entries = []
    auto_hub = len(entries) > 1 and not settings.strict_workspace_isolation
    if candidate_db.parent.exists():
        update: dict[str, Any] = {"db_path": candidate_db, "vector_db_path": candidate_vec}
        if auto_hub:
            update["hub_mode"] = True
        return settings.model_copy(update=update)
    if len(entries) == 1:
        only = entries[0]
        return settings.model_copy(
            update={
                "db_path": Path(only.db_path),
                "vector_db_path": Path(only.vector_path),
                "workspace_id": only.id,
            }
        )
    if auto_hub:
        first = entries[0]
        return settings.model_copy(
            update={
                "db_path": Path(first.db_path),
                "vector_db_path": Path(first.vector_path),
                "workspace_id": first.id,
                "hub_mode": True,
            }
        )
    return settings
