"""All MCP tool schemas, concatenated from per-domain modules."""

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
from agent_memory_lite.mcp.stdio_tools_p1 import P1_TOOLS
from agent_memory_lite.mcp.stdio_tools_research import RESEARCH_TOOLS
from agent_memory_lite.mcp.stdio_tools_review import REVIEW_TOOLS
from agent_memory_lite.mcp.stdio_tools_review_queue import REVIEW_QUEUE_TOOLS
from agent_memory_lite.mcp.stdio_tools_state_snapshots import STATE_SNAPSHOT_TOOLS
from agent_memory_lite.mcp.stdio_tools_theories import THEORY_TOOLS
from agent_memory_lite.mcp.stdio_tools_v3 import V3_TOOLS

ALL_TOOLS: list[types.Tool] = [
    *EPISODE_TOOLS,
    *CODE_TOOLS,
    *COORDINATION_TOOLS,
    *DIGEST_TOOLS,
    *DECISION_TOOLS,
    *COMPOUND_TOOLS,
    *REVIEW_TOOLS,
    *CAPABILITY_LINK_TOOLS,
    *THEORY_TOOLS,
    *RESEARCH_TOOLS,
    *CAPABILITY_TOOLS,
    *ARCHIVE_TOOLS,
    *P1_TOOLS,
    *STATE_SNAPSHOT_TOOLS,
    *REVIEW_QUEUE_TOOLS,
    # v3 surface — alongside v2 with `memory_v3_*` prefix to avoid collisions.
    *V3_TOOLS,
]
