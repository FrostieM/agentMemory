"""Write links between agent capabilities and research memory."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.capability_links import CapabilityLink, CapabilityLinkIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.capability_links_repo import (
    get_capability_link_by_unique,
    resolve_capability,
    resolve_target_workspace,
    upsert_capability_link_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def link_capability(conn: sqlite3.Connection, payload: CapabilityLinkIn) -> CapabilityLink:
    target_workspace = resolve_target_workspace(
        conn,
        target_type=payload.target_type,
        target_id=payload.target_id,
    )
    if target_workspace is None:
        raise NotFoundError(f"{payload.target_type.value} target {payload.target_id!r} not found")
    if target_workspace != payload.workspace_id:
        raise ValidationError("target_id must belong to the same workspace")

    capability = resolve_capability(
        conn,
        workspace_id=payload.workspace_id,
        capability_type=payload.capability_type,
        capability_id=payload.capability_id,
        capability_name=payload.capability_name,
    )
    if capability is None:
        raise NotFoundError(
            f"{payload.capability_type.value} capability "
            f"{payload.capability_id or payload.capability_name!r} not found"
        )
    capability_id, capability_name = capability

    link_id = new_id(IdKind.CAPABILITY_LINK)
    timestamp = iso_now()
    with with_tx(conn):
        upsert_capability_link_row(
            conn,
            link_id=link_id,
            workspace_id=payload.workspace_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            capability_type=payload.capability_type,
            capability_id=capability_id,
            capability_name=capability_name,
            relation=payload.relation,
            rationale=payload.rationale,
            strength=payload.strength,
            source_episode_id=payload.source_episode_id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = get_capability_link_by_unique(
            conn,
            workspace_id=payload.workspace_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            capability_type=payload.capability_type,
            capability_id=capability_id,
            relation=payload.relation,
        )
        assert stored is not None
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="link_capability",
            target_type="capability_link",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={
                "target_type": payload.target_type.value,
                "target_id": payload.target_id,
                "capability_type": payload.capability_type.value,
                "capability_id": capability_id,
                "relation": payload.relation.value,
                "strength": payload.strength,
            },
        )

    stored = get_capability_link_by_unique(
        conn,
        workspace_id=payload.workspace_id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        capability_type=payload.capability_type,
        capability_id=capability_id,
        relation=payload.relation,
    )
    assert stored is not None
    return stored
