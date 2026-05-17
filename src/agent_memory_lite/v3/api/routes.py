"""v3 HTTP routes — mounts at /v3/memory/* on the existing FastAPI app.

The 6 core tools + 2 hook primitives + memory_invoke_skill + memory_rollback,
all returning the uniform ``Envelope`` shape. Each route is a thin
wrapper around the v3 storage / cognition functions; no business
logic lives here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from agent_memory_lite.api.deps import DbDep
from agent_memory_lite.v3.api.schemas import (
    ArchiveRequest,
    EditRequest,
    Envelope,
    LintRequest,
    PinRequest,
    RollbackRequest,
    SearchRequest,
    WriteRequest,
)
from agent_memory_lite.v3.cognition.brief import compose_brief, fetch_skill_body
from agent_memory_lite.v3.cognition.impact_check import impact_check
from agent_memory_lite.v3.cognition.lint import lint as run_lint
from agent_memory_lite.v3.storage.reader import (
    count_kind,
    get_object,
    list_kind,
    search,
)
from agent_memory_lite.v3.storage.writer import (
    archive,
    edit,
    list_versions,
    pin,
    rollback,
    write,
)

router = APIRouter(prefix="/v3/memory", tags=["v3"])


def _ok(data: dict[str, Any] | list[Any] | None) -> Envelope:
    return Envelope(ok=True, data=data)


def _err(code: str, message: str) -> Envelope:
    return Envelope(ok=False, error={"code": code, "message": message})


# ============================================================
# memory_view (list_kind / get_object)
# ============================================================


@router.get("/list", response_model=Envelope)
def list_endpoint(
    conn: DbDep,
    workspace_id: str = Query(min_length=1),
    kind: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    pinned_only: bool = Query(default=False),
    status: str | None = Query(default=None),
) -> Envelope:
    """List rows of a kind as compact projections."""
    rows = list_kind(
        conn,
        workspace_id=workspace_id,
        kind=kind,
        limit=limit,
        pinned_only=pinned_only,
        status=status,
    )
    return _ok(rows)


@router.get("/get", response_model=Envelope)
def get_endpoint(
    conn: DbDep,
    workspace_id: str = Query(min_length=1),
    kind: str = Query(min_length=1),
    id: str = Query(min_length=1),  # noqa: A002 — v3 wire-shape field name
    fields: str | None = Query(default=None, description="Comma-separated extra columns"),
) -> Envelope:
    """Fetch one row by id. Returns compact projection by default."""
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    obj = get_object(conn, workspace_id=workspace_id, kind=kind, object_id=id, fields=field_list)
    if obj is None:
        return _err("not_found", f"{kind}:{id} not found in {workspace_id}")
    return _ok(obj)


@router.get("/count", response_model=Envelope)
def count_endpoint(
    conn: DbDep,
    workspace_id: str = Query(min_length=1),
    kind: str = Query(min_length=1),
    pinned_only: bool = Query(default=False),
    status: str | None = Query(default=None),
) -> Envelope:
    n = count_kind(
        conn, workspace_id=workspace_id, kind=kind, pinned_only=pinned_only, status=status
    )
    return _ok({"count": n})


# ============================================================
# memory_search
# ============================================================


@router.post("/search", response_model=Envelope)
def search_endpoint(req: SearchRequest, conn: DbDep) -> Envelope:
    hits = search(
        conn,
        workspace_id=req.workspace_id,
        query=req.query,
        kinds=req.kinds,
        limit=req.limit,
        rerank=req.rerank,
    )
    data = [{"kind": h.kind, "projection": h.projection, "score": h.score} for h in hits]
    return _ok(data)


# ============================================================
# memory_write / memory_edit / memory_pin / memory_archive
# ============================================================


@router.post("/write", response_model=Envelope)
def write_endpoint(req: WriteRequest, conn: DbDep) -> Envelope:
    out = write(
        conn,
        workspace_id=req.workspace_id,
        kind=req.kind,
        payload=req.payload,
        agent_id=req.agent_id,
        source_episode_id=req.source_episode_id,
    )
    if out is None:
        return _err("unsupported_kind", f"v3 writer does not support kind={req.kind}")
    return _ok(out)


@router.post("/edit", response_model=Envelope)
def edit_endpoint(req: EditRequest, conn: DbDep) -> Envelope:
    out = edit(
        conn,
        workspace_id=req.workspace_id,
        kind=req.kind,
        object_id=req.id,
        fields=req.fields,
        agent_id=req.agent_id,
    )
    if out is None:
        return _err("not_found", f"{req.kind}:{req.id} missing or no fields supplied")
    return _ok(out)


@router.post("/pin", response_model=Envelope)
def pin_endpoint(req: PinRequest, conn: DbDep) -> Envelope:
    out = pin(
        conn,
        workspace_id=req.workspace_id,
        kind=req.kind,
        object_id=req.id,
        pinned=req.pinned,
        agent_id=req.agent_id,
    )
    if out is None:
        return _err("unsupported_kind", "pin only valid for decision + behavior")
    return _ok(out)


@router.post("/archive", response_model=Envelope)
def archive_endpoint(req: ArchiveRequest, conn: DbDep) -> Envelope:
    out = archive(
        conn,
        workspace_id=req.workspace_id,
        kind=req.kind,
        object_id=req.id,
        reason=req.reason,
        agent_id=req.agent_id,
    )
    if out is None:
        return _err("not_found_or_unsupported", f"cannot archive {req.kind}:{req.id}")
    return _ok(out)


# ============================================================
# memory_brief / memory_lint / memory_invoke_skill / memory_rollback / memory_versions
# ============================================================


@router.get("/brief", response_model=Envelope)
def brief_endpoint(
    conn: DbDep,
    workspace_id: str = Query(min_length=1),
    task: str | None = Query(default=None),
    max_tokens: int = Query(default=500, ge=100, le=2000),
) -> Envelope:
    """Compose ≤max_tokens session-start brief from compact projections."""
    b = compose_brief(conn, workspace_id=workspace_id, task=task, max_tokens=max_tokens)
    return _ok(
        {
            "body_md": b.body_md,
            "token_count": b.token_count,
            "cache_hit": b.cache_hit,
            "sections": [s.name for s in b.sections],
        }
    )


@router.post("/lint", response_model=Envelope)
def lint_endpoint(req: LintRequest, conn: DbDep) -> Envelope:
    """Pre-task advisory. Wraps enforcement/dispatch with v3 retrievals."""
    result = run_lint(
        conn,
        workspace_id=req.workspace_id,
        tool_name=req.tool_name,
        tool_payload=req.tool_payload,
        transcript_path=req.transcript_path,
    )
    return _ok(result.to_dict())


@router.get("/skill/{skill_id}", response_model=Envelope)
def invoke_skill_endpoint(
    skill_id: str,
    conn: DbDep,
    workspace_id: str = Query(min_length=1),
) -> Envelope:
    """Return skill body_md and bump usage_count. Used by memory_invoke_skill."""
    out = fetch_skill_body(conn, workspace_id=workspace_id, skill_id=skill_id)
    if out is None:
        return _err("not_found", f"skill:{skill_id} not in {workspace_id}")
    return _ok(out)


@router.get("/impact_check", response_model=Envelope)
def impact_check_endpoint(
    conn: DbDep,
    workspace_id: str = Query(min_length=1),
    file_path: str = Query(min_length=1),
    callers_limit: int = Query(default=20, ge=1, le=100),
    hot_threshold: int = Query(default=3, ge=1, le=20),
) -> Envelope:
    """Pre-edit / pre-read impact analysis. Discipline primitive.

    Replaces the 3-call sequence (memory_file_digest +
    memory_graph_neighbors + ad-hoc analysis) with one envelope:
    digest + callers + hot_symbols + verdict + advisory.

    Verdict rollup:
      - not_indexed: file has no code_digests row
      - low:        0 callers
      - medium:     1-5 callers, no concentration
      - high:       6+ callers OR any symbol with >= hot_threshold callers

    Failure-soft: schema mismatch or missing file → not_indexed.
    """
    report = impact_check(
        conn,
        workspace_id=workspace_id,
        file_path=file_path,
        callers_limit=callers_limit,
        hot_threshold=hot_threshold,
    )
    return _ok(report.to_dict())


@router.post("/rollback", response_model=Envelope)
def rollback_endpoint(req: RollbackRequest, conn: DbDep) -> Envelope:
    out = rollback(
        conn,
        workspace_id=req.workspace_id,
        kind=req.kind,
        object_id=req.id,
        to_version=req.to_version,
        agent_id=req.agent_id,
        why=req.why,
    )
    if out is None:
        return _err("not_found_or_invalid", "rollback failed: missing version or empty why")
    return _ok(out)


@router.get("/versions", response_model=Envelope)
def versions_endpoint(
    conn: DbDep,
    workspace_id: str = Query(min_length=1),
    kind: str = Query(min_length=1),
    id: str = Query(min_length=1),  # noqa: A002 — v3 wire-shape field name
) -> Envelope:
    """List version history for a target, newest first."""
    rows = list_versions(conn, workspace_id=workspace_id, kind=kind, object_id=id)
    return _ok(rows)
