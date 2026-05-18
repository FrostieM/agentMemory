"""All MCP tool schemas, concatenated from per-domain modules.

MEMORY_TOOLS now use canonical names (``memory_*``, no ``_v3_`` infix).
Where a canonical name collides with a legacy v2 tool of the same
name (``memory_search``, ``memory_pin``, ``memory_archive``), the
canonical implementation wins via the dedup pass below — the
legacy spec is dropped from the advertised list so the MCP client
sees ONE tool per name. Dispatch is handled separately in
``stdio_handlers.py`` where MEMORY_HANDLERS is spread last and overrides
the same legacy keys for the same reason.
"""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_tools_archive import ARCHIVE_TOOLS
from agent_memory_lite.mcp.stdio_tools_capabilities import CAPABILITY_TOOLS
from agent_memory_lite.mcp.stdio_tools_capability import CAPABILITY_LINK_TOOLS
from agent_memory_lite.mcp.stdio_tools_code import CODE_TOOLS
from agent_memory_lite.mcp.stdio_tools_compound import COMPOUND_TOOLS
from agent_memory_lite.mcp.stdio_tools_coordination import COORDINATION_TOOLS
from agent_memory_lite.mcp.stdio_tools_decisions import DECISION_TOOLS
from agent_memory_lite.mcp.stdio_tools_digests import DIGEST_TOOLS
from agent_memory_lite.mcp.stdio_tools_episodes import EPISODE_TOOLS
from agent_memory_lite.mcp.stdio_tools_memory import MEMORY_TOOLS
from agent_memory_lite.mcp.stdio_tools_p1 import P1_TOOLS
from agent_memory_lite.mcp.stdio_tools_research import RESEARCH_TOOLS
from agent_memory_lite.mcp.stdio_tools_review import REVIEW_TOOLS
from agent_memory_lite.mcp.stdio_tools_review_queue import REVIEW_QUEUE_TOOLS
from agent_memory_lite.mcp.stdio_tools_state_snapshots import STATE_SNAPSHOT_TOOLS
from agent_memory_lite.mcp.stdio_tools_theories import THEORY_TOOLS


def _dedup_last_wins(*groups: list[types.Tool]) -> list[types.Tool]:
    """Concat tool groups and keep the LAST spec for any duplicated name.

    Used so the canonical (MEMORY_TOOLS) spec wins when a legacy v2 tool
    of the same name (``memory_search``, ``memory_pin``, etc.) is
    still in one of the earlier groups.
    """
    seen: dict[str, types.Tool] = {}
    for group in groups:
        for tool in group:
            seen[tool.name] = tool  # last write wins
    return list(seen.values())


ALL_TOOLS: list[types.Tool] = _dedup_last_wins(
    EPISODE_TOOLS,
    CODE_TOOLS,
    COORDINATION_TOOLS,
    DIGEST_TOOLS,
    DECISION_TOOLS,
    COMPOUND_TOOLS,
    REVIEW_TOOLS,
    CAPABILITY_LINK_TOOLS,
    THEORY_TOOLS,
    RESEARCH_TOOLS,
    CAPABILITY_TOOLS,
    ARCHIVE_TOOLS,
    P1_TOOLS,
    STATE_SNAPSHOT_TOOLS,
    REVIEW_QUEUE_TOOLS,
    # Canonical surface — overrides any legacy entry of the same name.
    MEMORY_TOOLS,
)
