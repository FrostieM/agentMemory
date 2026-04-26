from __future__ import annotations

import pytest

from agent_memory_lite.mcp.tools import TOOLS, dispatch


def test_tool_names_unique() -> None:
    names = [tool.name for tool in TOOLS]
    assert len(names) == len(set(names))


def test_known_tools_present() -> None:
    expected = {
        "memory_get_context",
        "memory_ingest_episode",
        "memory_ingest_file",
        "memory_write_decision",
        "memory_update_task_state",
    }
    assert expected.issubset({tool.name for tool in TOOLS})


def test_dispatch_unknown_tool_raises() -> None:
    with pytest.raises(KeyError, match="unknown MCP tool"):
        dispatch("memory_does_not_exist")
