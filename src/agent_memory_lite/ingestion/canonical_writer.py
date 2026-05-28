"""Canonical v3 write composition shared by HTTP and MCP.

The compact v3 surface is the only active agent write API, but a few
high-value kinds still need their business writers: decisions close
superseded rows, behaviors upsert by name, tasks upsert by task_id, and
plan_steps auto-assign rank. This module keeps those semantics behind
``memory_write`` instead of behind legacy route names.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.ingestion.canonical_domain_writers import (
    write_behavior_canonical,
    write_decision_canonical,
    write_theory_canonical,
)
from agent_memory_lite.ingestion.canonical_state_writers import (
    write_episode_canonical,
    write_task_canonical,
)
from agent_memory_lite.ingestion.plan_step_writer import add_plan_step_from_payload
from agent_memory_lite.storage.writer import write as storage_write
from agent_memory_lite.vector_store.base import VectorStore


def write_canonical(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    payload: dict[str, Any],
    agent_id: str = "agent",
    source_episode_id: str | None = None,
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> dict[str, Any] | None:
    """Write one v3 object and return the compact projection.

    The caller-supplied ``workspace_id`` is authoritative; an embedded
    ``payload.workspace_id`` cannot retarget the write.
    """
    body = {**payload, "workspace_id": workspace_id}
    if source_episode_id is not None:
        body["source_episode_id"] = source_episode_id

    result: dict[str, Any] | None
    if kind == "plan_step":
        result = add_plan_step_from_payload(
            conn,
            workspace_id=workspace_id,
            payload=payload,
            agent_id=agent_id,
            source_episode_id=source_episode_id,
        )
    elif kind == "decision":
        result = write_decision_canonical(
            conn, workspace_id=workspace_id, payload=body, settings=settings
        )
    elif kind == "theory":
        result = write_theory_canonical(
            conn, workspace_id=workspace_id, payload=body, settings=settings
        )
    elif kind == "behavior":
        result = write_behavior_canonical(conn, workspace_id=workspace_id, payload=body)
    elif kind == "episode":
        result = write_episode_canonical(
            conn,
            workspace_id=workspace_id,
            payload=body,
            settings=settings,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
    elif kind == "task":
        result = write_task_canonical(conn, workspace_id=workspace_id, payload=body)
    else:
        if kind == "skill":
            payload = {
                **payload,
                "subtype": payload.get("subtype") or "skill",
                "when_to_use_short": payload.get("when_to_use_short")
                or payload.get("trigger")
                or "",
            }
        result = storage_write(
            conn,
            workspace_id=workspace_id,
            kind=kind,
            payload=payload,
            agent_id=agent_id,
            source_episode_id=source_episode_id,
        )
    return result
