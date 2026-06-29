"""MCP handlers for the canonical surface — thin wrappers around canonical storage / cognition.

Each handler:

1. Normalises payload + applies workspace guard (read or write intent).
2. Calls the corresponding ``agent_memory_lite.*`` backend function
   with ``_runtime.db_for(workspace_id)`` so the explicit ``workspace_id``
   routes through the registry. A misconfigured anchor (e.g. server
   bound to ``copyBot`` while operator asks for ``agent-memory-lite``)
   still hits the right DB when the workspace is in the registry; the
   anchor remains the fallback for unregistered ids.
3. Returns the SAME envelope shape the HTTP routes return:
   ``{"ok": bool, "data": ..., "error": {"code", "message"} | None}``.

This keeps MCP and HTTP wire-shapes identical so a session log captured
over MCP can be replayed against the HTTP API for the e2e gate.

No business logic lives here. If a write fails (unsupported_kind,
not_found, …), the handler returns an envelope with ``ok=False`` and
the same error code the HTTP route would have returned. The MCP
dispatcher serializes the dict to JSON for the client.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agent_memory_lite.cognition.impact_check import impact_check
from agent_memory_lite.config.workspace_read_guard import read_workspace_unavailable_reason
from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.storage.reader import get_object, plan_for_task, search
from agent_memory_lite.storage.writer import archive, edit, pin

# ============================================================
# Envelope helpers (mirror api/routes/memory._ok / _err)
# ============================================================


def _ok(data: dict[str, Any] | list[Any] | None) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


def _read_guard(workspace_id: str) -> tuple[Any, dict[str, Any] | None]:
    """Resolve the workspace's conn + refuse a DEFINITE cross-workspace mismatch.

    The read analogue of the write handlers' ``ensure_workspace_matches_db``. It
    shares the FastAPI-free ``read_workspace_unavailable_reason`` decision with the
    HTTP ``ensure_workspace_readable_db`` guard (so the two stay in lockstep) and
    NOT ``api.workspace_routing`` -> ``api.errors`` -> FastAPI, so the discipline's
    first MCP call (impact_check / brief / search) never pays the FastAPI
    cold-start cost the write handler defers. Soft like its HTTP twin: refuses ONLY
    a definite physical-file mismatch; ambiguous / unregistered-non-anchor /
    in-memory / anchor / corrupt-registry reads stay readable. Returns
    ``(conn, None)`` on success or ``(conn, err_envelope)`` with a
    ``workspace_unavailable`` code on refusal.
    """
    conn = _runtime.db_for(workspace_id)
    reason = read_workspace_unavailable_reason(conn, workspace_id, _runtime.settings)
    if reason is not None:
        return conn, _err(
            "workspace_unavailable",
            f"workspace_id={workspace_id!r} is unavailable on this connection: "
            f"{reason}; refusing to avoid surfacing another workspace's rows.",
        )
    return conn, None


def _parse_fields(raw: Any) -> list[str] | None:
    """Accept ``fields`` as either CSV string or list[str]. Returns None for falsy."""
    if not raw:
        return None
    if isinstance(raw, list):
        return [str(f).strip() for f in raw if str(f).strip()]
    if isinstance(raw, str):
        return [f.strip() for f in raw.split(",") if f.strip()]
    return None


def _validate_episode_payload(
    *,
    workspace_id: str,
    body: dict[str, Any],
    source_episode_id: Any,
) -> None:
    payload = {**body, "workspace_id": workspace_id}
    if source_episode_id is not None:
        payload["source_episode_id"] = source_episode_id
    EpisodeIn(**payload)


# ============================================================
# Read handlers
# ============================================================


def _handle_v3_search(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    query = str(payload.get("query") or "")
    if not query:
        return _err("invalid_args", "query is required")
    kinds = payload.get("kinds")
    if kinds is not None and not isinstance(kinds, list):
        return _err("invalid_args", "kinds must be a list of strings")
    if isinstance(kinds, list) and not all(isinstance(k, str) for k in kinds):
        return _err("invalid_args", "kinds must be a list of strings")
    limit = int(payload.get("limit") or 10)
    rerank = bool(payload.get("rerank") or False)
    conn, guard_err = _read_guard(workspace_id)
    if guard_err is not None:
        return guard_err
    try:
        hits = search(
            conn,
            workspace_id=workspace_id,
            query=query,
            kinds=kinds,
            limit=limit,
            rerank=rerank,
        )
    except ValueError as exc:
        return _err("invalid_args", str(exc))
    data = [{"kind": h.kind, "projection": h.projection, "score": h.score} for h in hits]
    return _ok(data)


def _handle_v3_get(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    kind = str(payload.get("kind") or "")
    object_id = str(payload.get("id") or "")
    if not kind or not object_id:
        return _err("invalid_args", "kind and id are required")
    fields = _parse_fields(payload.get("fields"))
    conn, guard_err = _read_guard(workspace_id)
    if guard_err is not None:
        return guard_err
    try:
        obj = get_object(
            conn,
            workspace_id=workspace_id,
            kind=kind,
            object_id=object_id,
            fields=fields,
        )
    except ValueError as exc:
        return _err("validation_failed", str(exc))
    if obj is None:
        return _err("not_found", f"{kind}:{object_id} not found in {workspace_id}")
    return _ok(obj)


def _handle_v3_plan(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    task_id = str(payload.get("task_id") or "")
    if not task_id:
        return _err("invalid_args", "task_id is required")
    conn, guard_err = _read_guard(workspace_id)
    if guard_err is not None:
        return guard_err
    steps = plan_for_task(
        conn,
        workspace_id=workspace_id,
        task_id=task_id,
    )
    return _ok(steps)


# ============================================================
# Write handlers
# ============================================================


def _handle_v3_write(args: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0911 - guard-clause dispatch: validation + SQL/vector routing backstops each reject early
    # Lazy imports: write_canonical drags in the ingestion stack and api.errors
    # drags in FastAPI (~0.5s of cold module import) that NO read tool touches.
    # Deferring them here keeps the common first MCP call (impact_check / brief /
    # search, per the discipline rules) off that startup cost -- it is paid only
    # on the first write. See the first-call-hang investigation.
    from agent_memory_lite.api.errors import MemoryServiceError  # noqa: PLC0415
    from agent_memory_lite.api.workspace_routing import (  # noqa: PLC0415
        ensure_store_matches_workspace,
        ensure_workspace_matches_db,
    )
    from agent_memory_lite.ingestion.canonical_writer import (  # noqa: PLC0415
        write_canonical,
    )

    payload = _with_workspace(args, intent="write")
    workspace_id = str(payload["workspace_id"])
    kind = str(payload.get("kind") or "")
    body = payload.get("payload")
    if not kind or not isinstance(body, dict):
        return _err("invalid_args", "kind + payload object are required")
    conn = _runtime.db_for(workspace_id)
    # Physical-file backstop (audit C1/F1): the routed conn MUST be this
    # workspace's own DB -- enforced for the anchor AND foreign writes (matching
    # the HTTP guard). db_for re-resolves even the anchor through the registry,
    # so a stale/divergent anchor entry can misroute; never skip the check.
    try:
        ensure_workspace_matches_db(conn, workspace_id, _runtime.settings)
    except MemoryServiceError as exc:
        return _err(
            exc.error_code, "write rejected: routed DB does not match the workspace's registered DB"
        )
    agent_id = str(payload.get("agent_id") or "mcp")
    source_episode_id = payload.get("source_episode_id")
    embedding_provider = vector_store = None
    if kind == "episode":
        try:
            _validate_episode_payload(
                workspace_id=workspace_id,
                body=body,
                source_episode_id=source_episode_id,
            )
        except ValidationError as exc:
            return _err("invalid_args", f"invalid episode payload: {exc}")
        # Vector-side physical backstop (audit C1/F3): episode vectors route on a
        # path SEPARATE from the SQL conn, so verify the store is this
        # workspace's own .lance BEFORE embedding -- the LanceDB mirror of the
        # SQL ensure_workspace_matches_db run above. Checked before provider() so
        # a misroute fails fast without paying to load the embedding model.
        vector_store = _runtime.store_for(workspace_id)
        try:
            ensure_store_matches_workspace(vector_store, workspace_id, _runtime.settings)
        except MemoryServiceError as exc:
            return _err(
                exc.error_code,
                "write rejected: episode vectors routed to the wrong vector store",
            )
        embedding_provider = _runtime.provider()
    try:
        out = write_canonical(
            conn,
            workspace_id=workspace_id,
            kind=kind,
            payload=body,
            agent_id=agent_id,
            source_episode_id=source_episode_id,
            settings=_runtime.settings,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
    except ValidationError as exc:
        return _err("invalid_args", f"invalid {kind} payload: {exc}")
    except MemoryServiceError as exc:
        return _err(exc.error_code, str(exc))
    if out is None:
        return _err("unsupported_kind", f"writer does not support kind={kind}")
    return _ok(out)


def _handle_v3_edit(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="write")
    workspace_id = str(payload["workspace_id"])
    kind = str(payload.get("kind") or "")
    object_id = str(payload.get("id") or "")
    fields = payload.get("fields")
    if not kind or not object_id or not isinstance(fields, dict):
        return _err("invalid_args", "kind, id, and fields object are required")
    from agent_memory_lite.api.errors import MemoryServiceError  # noqa: PLC0415
    from agent_memory_lite.api.workspace_routing import (  # noqa: PLC0415
        ensure_workspace_matches_db,
    )

    conn = _runtime.db_for(workspace_id)
    try:
        ensure_workspace_matches_db(conn, workspace_id, _runtime.settings)
    except MemoryServiceError as exc:
        return _err(
            exc.error_code, "write rejected: routed DB does not match the workspace's registered DB"
        )
    out = edit(
        conn,
        workspace_id=workspace_id,
        kind=kind,
        object_id=object_id,
        fields=fields,
        agent_id=str(payload.get("agent_id") or "mcp"),
    )
    if out is None:
        return _err("not_found", f"{kind}:{object_id} missing or no fields supplied")
    return _ok(out)


def _handle_v3_pin(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="write")
    workspace_id = str(payload["workspace_id"])
    kind = str(payload.get("kind") or "")
    object_id = str(payload.get("id") or "")
    if not kind or not object_id:
        return _err("invalid_args", "kind and id are required")
    pinned_flag = payload.get("pinned")
    pinned = True if pinned_flag is None else bool(pinned_flag)
    from agent_memory_lite.api.errors import MemoryServiceError  # noqa: PLC0415
    from agent_memory_lite.api.workspace_routing import (  # noqa: PLC0415
        ensure_workspace_matches_db,
    )

    conn = _runtime.db_for(workspace_id)
    try:
        ensure_workspace_matches_db(conn, workspace_id, _runtime.settings)
    except MemoryServiceError as exc:
        return _err(
            exc.error_code, "write rejected: routed DB does not match the workspace's registered DB"
        )
    out = pin(
        conn,
        workspace_id=workspace_id,
        kind=kind,
        object_id=object_id,
        pinned=pinned,
        agent_id=str(payload.get("agent_id") or "mcp"),
    )
    if out is None:
        return _err("unsupported_kind", "pin only valid for decision + behavior")
    return _ok(out)


def _handle_v3_archive(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="write")
    workspace_id = str(payload["workspace_id"])
    kind = str(payload.get("kind") or "")
    object_id = str(payload.get("id") or "")
    if not kind or not object_id:
        return _err("invalid_args", "kind and id are required")
    from agent_memory_lite.api.errors import MemoryServiceError  # noqa: PLC0415
    from agent_memory_lite.api.workspace_routing import (  # noqa: PLC0415
        ensure_workspace_matches_db,
    )

    conn = _runtime.db_for(workspace_id)
    try:
        ensure_workspace_matches_db(conn, workspace_id, _runtime.settings)
    except MemoryServiceError as exc:
        return _err(
            exc.error_code, "write rejected: routed DB does not match the workspace's registered DB"
        )
    out = archive(
        conn,
        workspace_id=workspace_id,
        kind=kind,
        object_id=object_id,
        reason=payload.get("reason"),
        agent_id=str(payload.get("agent_id") or "mcp"),
    )
    if out is None:
        return _err("not_found_or_unsupported", f"cannot archive {kind}:{object_id}")
    return _ok(out)


# ============================================================
# Hook primitives + skill invocation
# ============================================================


def _handle_v3_brief(args: dict[str, Any]) -> dict[str, Any]:
    # Lazy import: cognition.brief drags in the brief-assembly + embeddings.base
    # stack (~0.17s of cold import) that only brief/invoke_skill need -- keep it
    # off the cold startup so a first impact_check/search call doesn't pay it.
    from agent_memory_lite.cognition.brief import compose_brief  # noqa: PLC0415

    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    max_tokens = int(payload.get("max_tokens") or 500)
    task = payload.get("task")
    raw_session = payload.get("session_id")
    session_id = str(raw_session) if isinstance(raw_session, str) and raw_session else None
    conn, guard_err = _read_guard(workspace_id)
    if guard_err is not None:
        return guard_err
    brief = compose_brief(
        conn,
        workspace_id=workspace_id,
        task=task if isinstance(task, str) else None,
        max_tokens=max_tokens,
        session_id=session_id,
    )
    return _ok(
        {
            "body_md": brief.body_md,
            "token_count": brief.token_count,
            "cache_hit": brief.cache_hit,
            "sections": [s.name for s in brief.sections],
        }
    )


def _handle_v3_lint(args: dict[str, Any]) -> dict[str, Any]:
    # Lazy import: cognition.lint pulls the enforcement/dispatch stack that only
    # the lint tool needs -- keep it off the cold startup import.
    from agent_memory_lite.cognition.lint import lint as run_lint  # noqa: PLC0415

    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    tool_name = str(payload.get("tool_name") or "")
    tool_payload = payload.get("tool_payload") or {}
    if not tool_name or not isinstance(tool_payload, dict):
        return _err("invalid_args", "tool_name + tool_payload object are required")
    transcript_path = payload.get("transcript_path")
    conn, guard_err = _read_guard(workspace_id)
    if guard_err is not None:
        return guard_err
    result = run_lint(
        conn,
        workspace_id=workspace_id,
        tool_name=tool_name,
        tool_payload=tool_payload,
        transcript_path=transcript_path if isinstance(transcript_path, str) else None,
    )
    return _ok(result.to_dict())


def _handle_v3_invoke_skill(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.cognition.brief import fetch_skill_body  # noqa: PLC0415

    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    skill_id = str(payload.get("skill_id") or "")
    if not skill_id:
        return _err("invalid_args", "skill_id is required")
    conn, guard_err = _read_guard(workspace_id)
    if guard_err is not None:
        return guard_err
    out = fetch_skill_body(
        conn,
        workspace_id=workspace_id,
        skill_id=skill_id,
    )
    if out is None:
        return _err("not_found", f"skill:{skill_id} not in {workspace_id}")
    return _ok(out)


def _handle_v3_impact_check(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    file_path = str(payload.get("file_path") or "")
    if not file_path:
        return _err("invalid_args", "file_path is required")
    callers_limit = int(payload.get("callers_limit") or 20)
    hot_threshold = int(payload.get("hot_threshold") or 3)
    conn, guard_err = _read_guard(workspace_id)
    if guard_err is not None:
        return guard_err
    report = impact_check(
        conn,
        workspace_id=workspace_id,
        file_path=file_path,
        callers_limit=callers_limit,
        hot_threshold=hot_threshold,
    )
    return _ok(report.to_dict())


def _handle_v3_status(args: dict[str, Any]) -> dict[str, Any]:
    """Diagnostic: anchor / hub_mode / registry / counts / migrations.

    Closes the "agent guesses its environment" footgun. Lazy imports
    keep the cold-start cost of stdio_handlers_memory minimal (status
    is a low-frequency call; routes-layer imports drag fastapi).
    """
    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    include_env = bool(payload.get("include_environment", True))
    # v3.1: opt-in to active-memory dashboard (defaults False to keep
    # the legacy payload shape for existing callers).
    include_active_memory = bool(payload.get("include_active_memory", False))
    from agent_memory_lite.api.routes.active_memory_status import (  # noqa: PLC0415
        build_active_memory,
    )
    from agent_memory_lite.api.routes.memory_status import (  # noqa: PLC0415
        build_environment,
    )
    from agent_memory_lite.api.routes.memory_status_queries import (  # noqa: PLC0415
        gather_adoption,
        gather_code_counts,
        gather_degradation,
        gather_memory_counts,
        max_ts,
        recent_actions_7d,
    )

    conn, guard_err = _read_guard(workspace_id)
    if guard_err is not None:
        return guard_err
    data: dict[str, Any] = {
        "version": _runtime_version(),
        "workspace_id": workspace_id,
        "memory": gather_memory_counts(conn, workspace_id).model_dump(),
        "code_memory": gather_code_counts(conn, workspace_id).model_dump(),
        "adoption": gather_adoption(conn, workspace_id).model_dump(),
        "last_episode_at": max_ts(
            conn, "SELECT MAX(created_at) FROM episodes WHERE workspace_id=?", workspace_id
        ),
        "last_decision_at": max_ts(
            conn, "SELECT MAX(updated_at) FROM decisions WHERE workspace_id=?", workspace_id
        ),
        "last_ingest_file_at": max_ts(
            conn,
            "SELECT MAX(created_at) FROM audit_log WHERE workspace_id=? AND action='ingest_file'",
            workspace_id,
        ),
        "recent_actions_7d": recent_actions_7d(conn, workspace_id),
        "degradation": gather_degradation(conn, workspace_id).model_dump(),
    }
    data["environment"] = (
        build_environment(conn, _runtime.settings).model_dump() if include_env else None
    )
    data["active_memory"] = (
        build_active_memory(conn, workspace_id).model_dump() if include_active_memory else None
    )
    return _ok(data)


def _runtime_version() -> str:
    from agent_memory_lite.version import __version__  # noqa: PLC0415

    return __version__


# ============================================================
# Dispatch table — name → handler
# ============================================================


MEMORY_HANDLERS: dict[str, Any] = {
    "memory_search": _handle_v3_search,
    "memory_get": _handle_v3_get,
    "memory_plan": _handle_v3_plan,
    "memory_write": _handle_v3_write,
    "memory_edit": _handle_v3_edit,
    "memory_pin": _handle_v3_pin,
    "memory_archive": _handle_v3_archive,
    "memory_brief": _handle_v3_brief,
    "memory_lint": _handle_v3_lint,
    "memory_invoke_skill": _handle_v3_invoke_skill,
    "memory_impact_check": _handle_v3_impact_check,
    "memory_status": _handle_v3_status,
}
