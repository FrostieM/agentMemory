"""Hub-registry path resolution for the MCP stdio runtime.

Lifted out of ``stdio_runtime.py`` so that module stays under the
150-SLOC ceiling. ``_Runtime.db_for``/``store_for`` call this to map a
``workspace_id`` to its ``(db_path, vector_path)`` pair; behavior is
unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_memory_lite.config.workspace_registry import WorkspaceRegistry

if TYPE_CHECKING:
    from agent_memory_lite.mcp.stdio_runtime import _Runtime


def _registry_paths_for(runtime: _Runtime, workspace_id: str) -> tuple[str, str] | None:
    """Look up ``(db_path, vector_path)`` for ``workspace_id`` in the hub registry."""
    try:
        registry = WorkspaceRegistry(runtime.settings.workspaces_file)
        entry = registry.get(workspace_id)
    except Exception:
        return None
    if entry is None or not entry.db_path:
        return None
    return entry.db_path, entry.vector_path or str(runtime.settings.vector_db_path)
