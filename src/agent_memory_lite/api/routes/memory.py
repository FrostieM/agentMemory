"""Canonical HTTP routes -- mounts at /memory/* on the FastAPI app.

The strict v3 tools + hook primitives all return the uniform ``Envelope`` shape. Each
route is a thin wrapper around the storage / cognition functions; no
business logic lives here.

Pre-2026-05-18 these lived at ``/memory/*`` with the legacy v2
routes at ``/memory/*``. After the canonical rename, the canonical surface
became the canonical surface and these routes moved to ``/memory/*``
directly; any colliding v2 routes (search, pin, archive) were
retired so there is one path per canonical name.
"""

from __future__ import annotations

import contextlib
import sqlite3
from typing import Any

from fastapi import APIRouter, Query
from pydantic import ValidationError as PydanticValidationError

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.errors import MemoryServiceError
from agent_memory_lite.api.errors import ValidationError as ApiValidationError
from agent_memory_lite.api.schemas.memory import (
    ArchiveRequest,
    EditRequest,
    Envelope,
    LintRequest,
    PinRequest,
    SearchRequest,
    WriteRequest,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.cognition.brief import compose_brief, fetch_skill_body
from agent_memory_lite.cognition.impact_check import impact_check
from agent_memory_lite.cognition.lint import lint as run_lint
from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.maintenance.implicit_feedback import record_implicit_archive
from agent_memory_lite.maintenance.sentinel_scheduler import maybe_run_sentinels
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.storage.reader import (
    get_object,
    plan_for_task,
    search,
)
from agent_memory_lite.storage.writer import (
    archive,
    edit,
    pin,
)

router = APIRouter(prefix="/memory", tags=["memory"])


def _ok(data: dict[str, Any] | list[Any] | None) -> Envelope:
    return Envelope(ok=True, data=data)


def _err(code: str, message: str) -> Envelope:
    return Envelope(ok=False, error={"code": code, "message": message})


def _maybe_autorun_sentinels(workspace_id: str, settings: SettingsDep) -> None:
    with contextlib.suppress(Exception):
        maybe_run_sentinels(
            workspace_id=workspace_id,
            settings=settings,
            db_path=settings.db_path,
            embedding_provider=None,
            vector_store=None,
        )


@router.get("/get", response_model=Envelope)
def get_endpoint(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
    kind: str = Query(min_length=1),
    id: str = Query(min_length=1),  # noqa: A002 -- wire-shape field name
    fields: str | None = Query(default=None, description="Comma-separated extra columns"),
) -> Envelope:
    """Fetch one row by id. Returns compact projection by default."""
    ensure_workspace_readable(workspace_id, settings)
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    try:
        obj = get_object(
            conn,
            workspace_id=workspace_id,
            kind=kind,
            object_id=id,
            fields=field_list,
        )
    except ValueError as exc:
        raise ApiValidationError(str(exc)) from exc
    if obj is None:
        return _err("not_found", f"{kind}:{id} not found in {workspace_id}")
    return _ok(obj)


@router.get("/plan", response_model=Envelope)
def plan_endpoint(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
    task_id: str = Query(min_length=1),
) -> Envelope:
    """List one plan's live steps as compact projections, rank-ordered."""
    ensure_workspace_readable(workspace_id, settings)
    steps = plan_for_task(conn, workspace_id=workspace_id, task_id=task_id)
    return _ok(steps)


# ============================================================
# memory_search
# ============================================================


@router.post("/search", response_model=Envelope)
def search_endpoint(req: SearchRequest, conn: DbDep, settings: SettingsDep) -> Envelope:
    ensure_workspace_readable(req.workspace_id, settings)
    _maybe_autorun_sentinels(req.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=req.workspace_id,
        endpoint="/memory/search",
        operation="search",
        label="Search memory",
        snippet=req.query,
    ) as trace:
        trace.stage_done(
            "input",
            "Search query accepted",
            counts={"limit": req.limit, "kinds": len(req.kinds or [])},
            snippet=req.query,
        )
        trace.stage_started("fts", "Compact memory lookup")
        hits = search(
            conn,
            workspace_id=req.workspace_id,
            query=req.query,
            kinds=req.kinds,
            limit=req.limit,
            rerank=req.rerank,
        )
        data = [{"kind": h.kind, "projection": h.projection, "score": h.score} for h in hits]
        trace.stage_done("fts", "Compact matches found", counts={"hits": len(data)})
        trace.stage_done("response", "Search response ready", counts={"hits": len(data)})
    if settings.audit_read_operations:
        with contextlib.suppress(sqlite3.Error):
            insert_audit(
                conn,
                workspace_id=req.workspace_id,
                action="search",
                target_type="search_query",
                target_id=req.query[:120],
                after={"limit": req.limit, "mode": "v3", "hits": len(data)},
            )
    return _ok(data)


# ============================================================
# memory_write / memory_edit / memory_pin / memory_archive
# ============================================================


# Round-2 audit (CRITICAL): the canonical write routes below dropped
# the ``ensure_workspace_writable`` guard their legacy predecessors
# (decisions.py / pin.py / archive.py) carried. A strict project chat
# could POST /memory/write {"workspace_id":"victim"} and mutate
# another project's memory — a direct breach of the first-class
# workspace-isolation invariant. Every write route now re-checks the
# guard before touching the storage layer.


@router.post("/write", response_model=Envelope)
def write_endpoint(req: WriteRequest, conn: DbDep, settings: SettingsDep) -> Envelope:
    ensure_workspace_writable(req.workspace_id, settings)
    try:
        out = write_canonical(
            conn,
            workspace_id=req.workspace_id,
            kind=req.kind,
            payload=req.payload,
            agent_id=req.agent_id,
            source_episode_id=req.source_episode_id,
            settings=settings,
        )
    except PydanticValidationError as exc:
        return _err("invalid_args", f"invalid {req.kind} payload: {exc}")
    except MemoryServiceError as exc:
        return _err(exc.error_code, str(exc))
    if out is None:
        return _err("unsupported_kind", f"writer does not support kind={req.kind}")
    return _ok(out)


@router.post("/edit", response_model=Envelope)
def edit_endpoint(req: EditRequest, conn: DbDep, settings: SettingsDep) -> Envelope:
    ensure_workspace_writable(req.workspace_id, settings)
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
def pin_endpoint(req: PinRequest, conn: DbDep, settings: SettingsDep) -> Envelope:
    ensure_workspace_writable(req.workspace_id, settings)
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
def archive_endpoint(req: ArchiveRequest, conn: DbDep, settings: SettingsDep) -> Envelope:
    ensure_workspace_writable(req.workspace_id, settings)
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
    with contextlib.suppress(Exception):
        record_implicit_archive(
            conn,
            settings=settings,
            workspace_id=req.workspace_id,
            source_type=req.kind,
            source_id=req.id,
        )
    return _ok(out)


# ============================================================
# memory_brief / memory_lint / memory_invoke_skill / memory_impact_check
# ============================================================


@router.get("/brief", response_model=Envelope)
def brief_endpoint(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
    task: str | None = Query(default=None),
    max_tokens: int = Query(default=500, ge=100, le=2000),
    session_id: str | None = Query(default=None, max_length=200),
) -> Envelope:
    """Compose ≤max_tokens session-start brief from compact projections.

    Pass ``session_id`` to opt into sticky-brief: the first call in a
    (workspace, session) pair gets the full budget; subsequent calls in
    the same session shrink to ``MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS``
    (default 200) so long chats stop paying the full token tax on every
    prompt.
    """
    ensure_workspace_readable(workspace_id, settings)
    _maybe_autorun_sentinels(workspace_id, settings)
    b = compose_brief(
        conn,
        workspace_id=workspace_id,
        task=task,
        max_tokens=max_tokens,
        session_id=session_id,
    )
    return _ok(
        {
            "body_md": b.body_md,
            "token_count": b.token_count,
            "cache_hit": b.cache_hit,
            "sections": [s.name for s in b.sections],
        }
    )


@router.post("/lint", response_model=Envelope)
def lint_endpoint(req: LintRequest, conn: DbDep, settings: SettingsDep) -> Envelope:
    """Pre-task advisory. Wraps enforcement/dispatch with canonical retrievals."""
    ensure_workspace_readable(req.workspace_id, settings)
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
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
) -> Envelope:
    """Return skill body_md and bump usage_count. Used by memory_invoke_skill."""
    ensure_workspace_readable(workspace_id, settings)
    out = fetch_skill_body(conn, workspace_id=workspace_id, skill_id=skill_id)
    if out is None:
        return _err("not_found", f"skill:{skill_id} not in {workspace_id}")
    return _ok(out)


@router.get("/impact_check", response_model=Envelope)
def impact_check_endpoint(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
    file_path: str = Query(min_length=1),
    callers_limit: int = Query(default=20, ge=1, le=100),
    hot_threshold: int = Query(default=3, ge=1, le=20),
) -> Envelope:
    """Pre-edit / pre-read impact analysis. Discipline primitive.

    Replaces the old multi-step digest + graph + ad-hoc analysis
    sequence with one envelope:
    digest + callers + hot_symbols + verdict + advisory.

    Verdict rollup:
      - not_indexed: file has no code_digests row
      - low:        0 callers
      - medium:     1-5 callers, no concentration
      - high:       6+ callers OR any symbol with >= hot_threshold callers

    Failure-soft: schema mismatch or missing file → not_indexed.
    """
    ensure_workspace_readable(workspace_id, settings)
    report = impact_check(
        conn,
        workspace_id=workspace_id,
        file_path=file_path,
        callers_limit=callers_limit,
        hot_threshold=hot_threshold,
    )
    return _ok(report.to_dict())
