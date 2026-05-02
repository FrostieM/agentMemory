"""GET-style listing route for theories.

Split out of ``theories.py`` so the routes file stays under the SLOC
ceiling. ``theories.py`` re-mounts this router on the same prefix.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.theory_responses import (
    to_evidence_response,
    to_theory_response,
)
from agent_memory_lite.api.schemas.theories import (
    ListTheoriesRequest,
    ListTheoriesResponse,
    TheoryWithEvidenceResponse,
)
from agent_memory_lite.repositories.theories_repo import (
    list_evidence_for_theory,
    list_theories,
)

router = APIRouter()


@router.post("/memory/list_theories", response_model=ListTheoriesResponse)
def list_theories_route(
    body: ListTheoriesRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListTheoriesResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    theories = list_theories(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        statuses=body.statuses,
        limit=body.limit,
        include_archived=body.include_archived,
        since=body.since,
        until=body.until,
    )
    return ListTheoriesResponse(
        theories=[
            TheoryWithEvidenceResponse(
                theory=to_theory_response(theory),
                evidence=[
                    to_evidence_response(evidence)
                    for evidence in (
                        list_evidence_for_theory(conn, theory.id, limit=body.evidence_limit)
                        if body.include_evidence
                        else []
                    )
                ],
            )
            for theory in theories
        ],
    )
