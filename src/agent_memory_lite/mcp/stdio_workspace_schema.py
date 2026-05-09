"""Resolve the JSON-Schema fragment used for ``workspace_id`` parameters.

Phase 2.7 of v2.2 consolidation. Split out of ``stdio_runtime.py`` to
honour the ≤150-SLOC-per-module ceiling once the resolution logic grew
beyond a single-line return.

Prior to Phase 2.7, the schema returned ``settings.workspace_id`` as-is.
In hub-mode chats with no ``MEMORY_WORKSPACE_ID`` env, that gave the
operator ``default: "default"`` in every tool schema — useless and
misleading because the runtime was actually routing to a real
registered workspace via the cwd-based resolver.

The fixed resolution chain (in order):

1. ``settings.workspace_id`` when explicitly set (≠ "default" sentinel).
2. Registry's first entry id when the placeholder is in effect and the
   registry has at least one entry.
3. ``"default"`` only when both above fail (registry empty or unreadable).
"""

from __future__ import annotations

from agent_memory_lite.config.workspace_registry import WorkspaceRegistry
from agent_memory_lite.mcp.stdio_runtime import _runtime


def workspace_schema() -> dict[str, str]:
    """Return the ``workspace_id`` JSON-Schema fragment with a useful default."""
    settings_ws = _runtime.settings.workspace_id
    if settings_ws and settings_ws != "default":
        return {"type": "string", "default": settings_ws}
    try:
        registry = WorkspaceRegistry(_runtime.settings.workspaces_file)
        entries = registry.list()
    except Exception:
        entries = []
    if entries:
        return {"type": "string", "default": entries[0].id}
    return {"type": "string", "default": "default"}
