"""Handlers for capability links + behavior instructions."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.mcp.stdio_guards import _with_workspace, _workspace_from_args
from agent_memory_lite.mcp.stdio_http import _http_write
from agent_memory_lite.mcp.stdio_payloads import (
    _behavior_instruction_payload,
    _capability_link_payload,
)
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.repositories.behavior_repo import list_behavior_instructions
from agent_memory_lite.repositories.capability_links_repo import list_capability_links


def _handle_link_capability(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/link_capability", payload, log_label="link_capability")
    if delegated is not None:
        return delegated

    return _capability_link_payload(link_capability(_runtime.db(), CapabilityLinkIn(**payload)))


def _handle_list_capability_links(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.models.enums import (  # noqa: PLC0415
        CapabilityLinkTargetType,
        CapabilityType,
    )

    workspace_id = _workspace_from_args(args, intent="read")
    return {
        "links": [
            _capability_link_payload(link)
            for link in list_capability_links(
                _runtime.db_for(workspace_id),
                workspace_id=workspace_id,
                target_type=(
                    CapabilityLinkTargetType(args["target_type"])
                    if args.get("target_type")
                    else None
                ),
                target_id=args.get("target_id"),
                capability_type=(
                    CapabilityType(args["capability_type"]) if args.get("capability_type") else None
                ),
                capability_id=args.get("capability_id"),
                limit=int(args.get("limit", 50)),
            )
        ]
    }


def _handle_upsert_behavior_instruction(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write(
        "/memory/upsert_behavior_instruction",
        payload,
        log_label="upsert_behavior_instruction",
    )
    if delegated is not None:
        return delegated

    instruction = upsert_behavior_instruction(_runtime.db(), BehaviorInstructionIn(**payload))
    return _behavior_instruction_payload(instruction)


def _handle_list_behavior_instructions(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.models.enums import BehaviorInstructionKind  # noqa: PLC0415

    workspace_id = _workspace_from_args(args, intent="read")
    raw_kinds = args.get("kinds")
    kinds = [BehaviorInstructionKind(item) for item in raw_kinds] if raw_kinds else None
    return {
        "instructions": [
            _behavior_instruction_payload(item)
            for item in list_behavior_instructions(
                _runtime.db_for(workspace_id),
                workspace_id=workspace_id,
                query=args.get("query"),
                kinds=kinds,
                include_inactive=bool(args.get("include_inactive", False)),
                limit=int(args.get("limit", 10)),
                since=args.get("since"),
                until=args.get("until"),
            )
        ]
    }
