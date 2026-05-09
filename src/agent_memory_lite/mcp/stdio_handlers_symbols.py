"""1.4.0: stdio handler for ``memory_find_symbols``.

Split out of ``stdio_handlers_episodes.py`` so that file stays under
the SLOC ceiling. Tries the warm HTTP service first (so a single-
workspace project mode benefits from middleware / audit), falls back
to local SQLite via ``tools_symbols.memory_find_symbols``.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_http import _http_read
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.mcp.tools_symbols import memory_find_symbols


def _handle_find_symbols(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_read("/memory/find_symbols", payload, log_label="find_symbols")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_find_symbols(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        name=payload.get("name"),
        name_prefix=payload.get("name_prefix"),
        kinds=payload.get("kinds"),
        languages=payload.get("languages"),
        limit=int(payload.get("limit", 20)),
    )
