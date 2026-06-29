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
import inspect
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from fastapi import APIRouter, Query, Request
from pydantic import ValidationError as PydanticValidationError

from agent_memory_lite.api import deps as api_deps
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
from agent_memory_lite.api.workspace_routing import (
    ensure_workspace_matches_db,
    ensure_workspace_readable_db,
)
from agent_memory_lite.cognition.brief import compose_brief, fetch_skill_body
from agent_memory_lite.cognition.impact_check import impact_check
from agent_memory_lite.cognition.lint import lint as run_lint
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.maintenance.implicit_feedback import record_implicit_archive
from agent_memory_lite.maintenance.sentinel_scheduler import maybe_run_sentinels
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.storage.reader import (
    get_object,
    list_plans,
    plan_for_task,
    search,
)
from agent_memory_lite.storage.writer import (
    archive,
    edit,
    pin,
)
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.factory import get_vector_store

router = APIRouter(prefix="/memory", tags=["memory"])


@dataclass(frozen=True)
class _ResolvedDependency:
    value: Any
    cleanup: Callable[[], None] | None = None


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


def _resolve_embedding_provider(
    request: Request, settings: SettingsDep
) -> tuple[EmbeddingProvider, Callable[[], None] | None]:
    override = request.app.dependency_overrides.get(api_deps.get_embedding_provider_dep)
    if override is not None:
        resolved = _call_lazy_override(override, request=request, settings=settings)
        return cast(EmbeddingProvider, resolved.value), resolved.cleanup
    return api_deps.get_embedding_provider_dep(settings), None


def _resolve_vector_store(
    request: Request, settings: SettingsDep
) -> tuple[VectorStore, Callable[[], None] | None]:
    override = request.app.dependency_overrides.get(api_deps.get_vector_store_dep)
    if override is not None:
        resolved = _call_lazy_override(override, request=request, settings=settings)
        return cast(VectorStore, resolved.value), resolved.cleanup
    store, owned = api_deps.resolve_vector_store_for_request(request, settings)
    return store, store.close if owned else None


def _call_lazy_override(
    override: Any, *, request: Request, settings: SettingsDep
) -> _ResolvedDependency:
    """Call common FastAPI dependency overrides outside FastAPI's solver."""
    try:
        signature = inspect.signature(override)
    except (TypeError, ValueError):
        return _wrap_dependency_result(override())

    required = [
        param
        for param in signature.parameters.values()
        if param.default is inspect.Parameter.empty
        and param.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    ]
    if not required:
        return _wrap_dependency_result(override())

    named: dict[str, Any] = {}
    for param in required:
        if _is_request_param(param):
            named[param.name] = request
        elif _is_settings_param(param):
            named[param.name] = settings
    if len(named) == len(required):
        return _wrap_dependency_result(override(**named))
    if len(required) == 1:
        param = required[0]
        return _wrap_dependency_result(
            override(request if _looks_like_request_name(param.name) else settings)
        )
    if len(required) == 2:
        return _wrap_dependency_result(override(request, settings))
    return _wrap_dependency_result(override())


def _wrap_dependency_result(result: Any) -> _ResolvedDependency:
    if inspect.isgenerator(result):
        return _enter_generator_dependency(result)
    if hasattr(result, "__enter__") and hasattr(result, "__exit__"):
        value = result.__enter__()

        def cleanup() -> None:
            result.__exit__(None, None, None)

        return _ResolvedDependency(value=value, cleanup=cleanup)
    return _ResolvedDependency(value=result)


def _enter_generator_dependency(generator: Any) -> _ResolvedDependency:
    try:
        value = next(generator)
    except StopIteration as exc:
        raise RuntimeError("dependency override did not yield") from exc

    def cleanup() -> None:
        try:
            next(generator)
        except StopIteration:
            return
        finally:
            generator.close()
        raise RuntimeError("dependency override yielded more than once")

    return _ResolvedDependency(value=value, cleanup=cleanup)


def _is_request_param(param: inspect.Parameter) -> bool:
    return param.name == "request" or _annotation_name(param.annotation) == "Request"


def _is_settings_param(param: inspect.Parameter) -> bool:
    return param.name == "settings" or _annotation_name(param.annotation) == "Settings"


def _looks_like_request_name(name: str) -> bool:
    return name in {"request", "req"}


def _annotation_name(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return ""
    if isinstance(annotation, str):
        return annotation.rsplit(".", 1)[-1].strip("'\"")
    return getattr(annotation, "__name__", "")


def _validate_episode_payload(req: WriteRequest) -> None:
    body = {**req.payload, "workspace_id": req.workspace_id}
    if req.source_episode_id is not None:
        body["source_episode_id"] = req.source_episode_id
    EpisodeIn(**body)


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
    ensure_workspace_readable_db(conn, workspace_id, settings)
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
    ensure_workspace_readable_db(conn, workspace_id, settings)
    steps = plan_for_task(conn, workspace_id=workspace_id, task_id=task_id)
    return _ok(steps)


@router.get("/plans", response_model=Envelope)
def plans_endpoint(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
) -> Envelope:
    """List the workspace's plans (tasks that own live steps) with per-status
    step counts, in-progress first then most-recently-updated. Powers the plan
    UI's picker so it auto-shows the current plan and paginates without a
    task_id."""
    ensure_workspace_readable(workspace_id, settings)
    ensure_workspace_readable_db(conn, workspace_id, settings)
    return _ok(list_plans(conn, workspace_id=workspace_id))


# ============================================================
# memory_search
# ============================================================


@router.post("/search", response_model=Envelope)
def search_endpoint(req: SearchRequest, conn: DbDep, settings: SettingsDep) -> Envelope:
    ensure_workspace_readable(req.workspace_id, settings)
    ensure_workspace_readable_db(conn, req.workspace_id, settings)
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
def write_route(
    req: WriteRequest,
    conn: DbDep,
    settings: SettingsDep,
    request: Request,
) -> Envelope:
    return write_endpoint(
        req,
        conn,
        settings,
        request=request,
    )


def _resolve_episode_write_dependencies(
    req: WriteRequest,
    settings: SettingsDep,
    *,
    request: Request | None,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    dependency_cleanups: list[Callable[[], None]],
) -> tuple[EmbeddingProvider, VectorStore]:
    _validate_episode_payload(req)
    resolved_vector_store = vector_store
    if resolved_vector_store is None:
        if request is None:
            resolved_vector_store = get_vector_store(settings)
        else:
            resolved_vector_store, vector_store_cleanup = _resolve_vector_store(request, settings)
            if vector_store_cleanup is not None:
                dependency_cleanups.append(vector_store_cleanup)

    resolved_embedding_provider = embedding_provider
    if resolved_embedding_provider is None:
        if request is None:
            resolved_embedding_provider = api_deps.get_embedding_provider_dep(settings)
        else:
            resolved_embedding_provider, provider_cleanup = _resolve_embedding_provider(
                request, settings
            )
            if provider_cleanup is not None:
                dependency_cleanups.append(provider_cleanup)

    return resolved_embedding_provider, resolved_vector_store


def write_endpoint(
    req: WriteRequest,
    conn: DbDep,
    settings: SettingsDep,
    request: Request | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> Envelope:
    dependency_cleanups: list[Callable[[], None]] = []
    try:
        ensure_workspace_writable(req.workspace_id, settings)
        ensure_workspace_matches_db(conn, req.workspace_id, settings)
        if req.kind == "episode":
            try:
                embedding_provider, vector_store = _resolve_episode_write_dependencies(
                    req,
                    settings,
                    request=request,
                    embedding_provider=embedding_provider,
                    vector_store=vector_store,
                    dependency_cleanups=dependency_cleanups,
                )
            except PydanticValidationError as exc:
                return _err("invalid_args", f"invalid {req.kind} payload: {exc}")
        try:
            out = write_canonical(
                conn,
                workspace_id=req.workspace_id,
                kind=req.kind,
                payload=req.payload,
                agent_id=req.agent_id,
                source_episode_id=req.source_episode_id,
                settings=settings,
                embedding_provider=embedding_provider,
                vector_store=vector_store,
            )
        except PydanticValidationError as exc:
            return _err("invalid_args", f"invalid {req.kind} payload: {exc}")
        except MemoryServiceError as exc:
            return _err(exc.error_code, str(exc))
        if out is None:
            return _err("unsupported_kind", f"writer does not support kind={req.kind}")
        return _ok(out)
    finally:
        for cleanup in reversed(dependency_cleanups):
            with contextlib.suppress(Exception):
                cleanup()


@router.post("/edit", response_model=Envelope)
def edit_endpoint(req: EditRequest, conn: DbDep, settings: SettingsDep) -> Envelope:
    ensure_workspace_writable(req.workspace_id, settings)
    ensure_workspace_matches_db(conn, req.workspace_id, settings)
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
    ensure_workspace_matches_db(conn, req.workspace_id, settings)
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
    ensure_workspace_matches_db(conn, req.workspace_id, settings)
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
    ensure_workspace_readable_db(conn, workspace_id, settings)
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
    ensure_workspace_readable_db(conn, req.workspace_id, settings)
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
    ensure_workspace_readable_db(conn, workspace_id, settings)
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
    ensure_workspace_readable_db(conn, workspace_id, settings)
    report = impact_check(
        conn,
        workspace_id=workspace_id,
        file_path=file_path,
        callers_limit=callers_limit,
        hot_threshold=hot_threshold,
    )
    return _ok(report.to_dict())
