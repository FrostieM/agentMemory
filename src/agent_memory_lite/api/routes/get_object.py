"""Discover-then-fetch endpoint.

The context envelope's `<index>` blocks list `id+title` for items that
didn't fit the full render. The agent picks an id, calls
`/memory/get_object` with `kind` + `id`, and gets the full body back.

Payload shapes and the kind dispatch live in
``get_object_payloads.py`` so this file stays a thin route.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import DbDep
from agent_memory_lite.api.routes.get_object_payloads import (
    KIND_FETCHERS,
    theory_payload,
)
from agent_memory_lite.repositories.theories_repo import (
    get_theory,
    list_evidence_for_theory,
)

SUPPORTED_KINDS = Literal[
    "decision",
    "theory",
    "snapshot",
    "experiment",
    "insight",
    "concept",
    "role",
    "skill",
    "playbook",
    "behavior_instruction",
]


class GetObjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    kind: SUPPORTED_KINDS
    id: str = Field(min_length=1)
    include_evidence: bool = False


class GetObjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    id: str
    workspace_id: str
    found: bool
    object: dict[str, Any] | None = None


router = APIRouter()


@router.post("/memory/get_object", response_model=GetObjectResponse)
def get_object_route(body: GetObjectRequest, conn: DbDep) -> GetObjectResponse:
    """Fetch one memory object by (kind, id).

    Designed for the discover-then-fetch pattern: the agent sees a
    `<ref id="..." title="..."/>` in the context envelope's `<index>`
    block and asks for the full body without a fuzzy ``list_*`` query.
    """
    workspace_id = body.workspace_id
    found_obj: dict[str, Any] | None = None

    if body.kind == "theory":
        theory = get_theory(conn, body.id)
        if theory is not None:
            evidence = (
                list_evidence_for_theory(conn, theory.id, limit=20) if body.include_evidence else []
            )
            found_obj = theory_payload(theory, evidence)
            workspace_id = theory.workspace_id
    elif body.kind in KIND_FETCHERS:
        fetcher, render = KIND_FETCHERS[body.kind]
        item = fetcher(conn, body.id)
        if item is not None:
            found_obj = render(item)
            workspace_id = item.workspace_id
    else:  # pragma: no cover - SUPPORTED_KINDS literal already constrains this
        raise HTTPException(status_code=400, detail=f"unsupported kind: {body.kind}")

    return GetObjectResponse(
        kind=body.kind,
        id=body.id,
        workspace_id=workspace_id,
        found=found_obj is not None,
        object=found_obj,
    )
