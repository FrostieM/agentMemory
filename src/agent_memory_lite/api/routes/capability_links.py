"""Routes for capability-to-research links."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.schemas.capability_links import (
    CapabilityLinkResponse,
    LinkCapabilityRequest,
    ListCapabilityLinksRequest,
    ListCapabilityLinksResponse,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.maintenance.implicit_feedback import record_implicit_link
from agent_memory_lite.models.capability_links import CapabilityLink, CapabilityLinkIn
from agent_memory_lite.repositories.capability_links_repo import list_capability_links

router = APIRouter()


def _link_response(link: CapabilityLink) -> CapabilityLinkResponse:
    return CapabilityLinkResponse(
        link_id=link.id,
        workspace_id=link.workspace_id,
        target_type=link.target_type,
        target_id=link.target_id,
        capability_type=link.capability_type,
        capability_id=link.capability_id,
        capability_name=link.capability_name,
        relation=link.relation,
        rationale=link.rationale,
        strength=link.strength,
        source_episode_id=link.source_episode_id,
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


@router.post("/memory/link_capability", response_model=CapabilityLinkResponse)
def link_capability_route(
    body: LinkCapabilityRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> CapabilityLinkResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/link_capability",
        operation="link_capability",
        label="Link capability",
        snippet=f"{body.capability_type}:{body.capability_name or body.capability_id}",
    ) as trace:
        trace.stage_done(
            "validate",
            "Capability link payload accepted",
            counts={"target_type": body.target_type, "capability_type": body.capability_type},
        )
        trace.stage_started("persist", "Persist capability link")
        link = link_capability(
            conn,
            CapabilityLinkIn(
                workspace_id=body.workspace_id,
                target_type=body.target_type,
                target_id=body.target_id,
                capability_type=body.capability_type,
                capability_id=body.capability_id,
                capability_name=body.capability_name,
                relation=body.relation,
                rationale=body.rationale,
                strength=body.strength,
                source_episode_id=body.source_episode_id,
            ),
        )
        trace.stage_done("persist", "Capability link persisted", counts={"relation": link.relation})
        trace.graph_delta(
            object_type="capability_link",
            object_id=link.id,
            action="created",
            label="Capability linked",
            counts={"target_id": link.target_id},
        )
        # v1.4 implicit feedback: link strength is the operator's
        # explicit weight on the target's importance.
        record_implicit_link(
            conn,
            settings=settings,
            workspace_id=body.workspace_id,
            target_type=body.target_type,
            target_id=body.target_id,
            strength=link.strength,
        )
        response = _link_response(link)
        trace.stage_done("response", "Capability link response ready", counts={"link_id": link.id})
        return response


@router.post("/memory/list_capability_links", response_model=ListCapabilityLinksResponse)
def list_capability_links_route(
    body: ListCapabilityLinksRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListCapabilityLinksResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    links = list_capability_links(
        conn,
        workspace_id=body.workspace_id,
        target_type=body.target_type,
        target_id=body.target_id,
        capability_type=body.capability_type,
        capability_id=body.capability_id,
        limit=body.limit,
    )
    return ListCapabilityLinksResponse(links=[_link_response(item) for item in links])
