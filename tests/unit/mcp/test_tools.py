from __future__ import annotations

import pytest

from agent_memory_lite.mcp.tools import TOOLS, dispatch


def test_tool_names_unique() -> None:
    names = [tool.name for tool in TOOLS]
    assert len(names) == len(set(names))


def test_known_tools_present() -> None:
    expected = {
        "memory_get_context",
        "memory_search",
        "memory_ingest_episode",
        "memory_ingest_file",
        "memory_list_candidates",
        "memory_promote_candidate",
        "memory_reject_candidate",
        "memory_list_maintenance_events",
        "memory_resolve_maintenance_event",
        "memory_link_capability",
        "memory_list_capability_links",
        "memory_upsert_behavior_instruction",
        "memory_list_behavior_instructions",
        "memory_write_decision",
        "memory_list_decisions",
        "memory_update_task_state",
        "memory_write_theory",
        "memory_add_theory_evidence",
        "memory_list_theories",
        "memory_register_snapshot",
        "memory_write_experiment",
        "memory_add_experiment_result",
        "memory_upsert_concept",
        "memory_distill_insight",
        "memory_update_insight",
        "memory_list_research_agenda",
        "memory_list_concepts",
        "memory_list_insights",
        "memory_upsert_agent_role",
        "memory_upsert_agent_skill",
        "memory_upsert_agent_playbook",
        "memory_list_agent_capabilities",
        "memory_record_usage_feedback",
    }
    assert expected.issubset({tool.name for tool in TOOLS})


def test_stdio_server_exposes_registry_tools() -> None:
    from agent_memory_lite.mcp.stdio_server import _TOOLS as STDIO_TOOLS  # noqa: PLC0415

    registry_names = {tool.name for tool in TOOLS}
    stdio_names = {tool.name for tool in STDIO_TOOLS}
    assert registry_names.issubset(stdio_names)


def test_stdio_server_strict_workspace_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_memory_lite.mcp import stdio_server  # noqa: PLC0415

    guarded_settings = stdio_server._runtime.settings.model_copy(
        update={
            "workspace_id": "project-a",
            "strict_workspace_isolation": True,
            "hub_mode": False,
        }
    )
    monkeypatch.setattr(stdio_server._runtime, "settings", guarded_settings)

    assert stdio_server._workspace_from_args({"workspace_id": "project-a"}) == "project-a"
    with pytest.raises(ValueError, match="MEMORY_STRICT_WORKSPACE_ISOLATION"):
        stdio_server._workspace_from_args({"workspace_id": "project-b"})


def test_dispatch_unknown_tool_raises() -> None:
    with pytest.raises(KeyError, match="unknown MCP tool"):
        dispatch("memory_does_not_exist")


def test_memory_kind_enums_include_plan_step() -> None:
    """plan_step is addressable through every generic memory_* tool that
    takes a kind from the shared _KINDS enum (get / write / edit / archive
    use ``kind``; search uses ``kinds``)."""
    from agent_memory_lite.mcp.stdio_tools_memory import MEMORY_TOOLS  # noqa: PLC0415

    by_name = {tool.name: tool for tool in MEMORY_TOOLS}
    for name in ("memory_get", "memory_write", "memory_edit", "memory_archive"):
        enum = by_name[name].inputSchema["properties"]["kind"]["enum"]
        assert "plan_step" in enum, f"{name} kind enum missing plan_step"
    search_enum = by_name["memory_search"].inputSchema["properties"]["kinds"]["items"]["enum"]
    assert "plan_step" in search_enum
