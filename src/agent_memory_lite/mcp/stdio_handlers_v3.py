"""MCP handlers for the v3 surface — thin wrappers around v3 storage / cognition.

Each handler:

1. Normalises payload + applies workspace guard (read or write intent).
2. Calls the corresponding ``agent_memory_lite.v3.*`` backend function
   with ``_runtime.db()``.
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

from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.v3.cognition.brief import compose_brief, fetch_skill_body
from agent_memory_lite.v3.cognition.impact_check import impact_check
from agent_memory_lite.v3.cognition.lint import lint as run_lint
from agent_memory_lite.v3.storage.reader import get_object, search
from agent_memory_lite.v3.storage.writer import archive, edit, pin, write

# ============================================================
# Envelope helpers (mirror v3/api/routes._ok / _err)
# ============================================================


def _ok(data: dict[str, Any] | list[Any] | None) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


def _parse_fields(raw: Any) -> list[str] | None:
    """Accept ``fields`` as either CSV string or list[str]. Returns None for falsy."""
    if not raw:
        return None
    if isinstance(raw, list):
        return [str(f).strip() for f in raw if str(f).strip()]
    if isinstance(raw, str):
        return [f.strip() for f in raw.split(",") if f.strip()]
    return None


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
    limit = int(payload.get("limit") or 10)
    rerank = bool(payload.get("rerank") or False)
    hits = search(
        _runtime.db(),
        workspace_id=workspace_id,
        query=query,
        kinds=kinds,
        limit=limit,
        rerank=rerank,
    )
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
    obj = get_object(
        _runtime.db(),
        workspace_id=workspace_id,
        kind=kind,
        object_id=object_id,
        fields=fields,
    )
    if obj is None:
        return _err("not_found", f"{kind}:{object_id} not found in {workspace_id}")
    return _ok(obj)


# ============================================================
# Write handlers
# ============================================================


def _handle_v3_write(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="write")
    workspace_id = str(payload["workspace_id"])
    kind = str(payload.get("kind") or "")
    body = payload.get("payload")
    if not kind or not isinstance(body, dict):
        return _err("invalid_args", "kind + payload object are required")
    out = write(
        _runtime.db(),
        workspace_id=workspace_id,
        kind=kind,
        payload=body,
        agent_id=str(payload.get("agent_id") or "mcp"),
        source_episode_id=payload.get("source_episode_id"),
    )
    if out is None:
        return _err("unsupported_kind", f"v3 writer does not support kind={kind}")
    return _ok(out)


def _handle_v3_edit(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="write")
    workspace_id = str(payload["workspace_id"])
    kind = str(payload.get("kind") or "")
    object_id = str(payload.get("id") or "")
    fields = payload.get("fields")
    if not kind or not object_id or not isinstance(fields, dict):
        return _err("invalid_args", "kind, id, and fields object are required")
    out = edit(
        _runtime.db(),
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
    out = pin(
        _runtime.db(),
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
    out = archive(
        _runtime.db(),
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
    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    max_tokens = int(payload.get("max_tokens") or 500)
    task = payload.get("task")
    brief = compose_brief(
        _runtime.db(),
        workspace_id=workspace_id,
        task=task if isinstance(task, str) else None,
        max_tokens=max_tokens,
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
    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    tool_name = str(payload.get("tool_name") or "")
    tool_payload = payload.get("tool_payload") or {}
    if not tool_name or not isinstance(tool_payload, dict):
        return _err("invalid_args", "tool_name + tool_payload object are required")
    transcript_path = payload.get("transcript_path")
    result = run_lint(
        _runtime.db(),
        workspace_id=workspace_id,
        tool_name=tool_name,
        tool_payload=tool_payload,
        transcript_path=transcript_path if isinstance(transcript_path, str) else None,
    )
    return _ok(result.to_dict())


def _handle_v3_invoke_skill(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    workspace_id = str(payload["workspace_id"])
    skill_id = str(payload.get("skill_id") or "")
    if not skill_id:
        return _err("invalid_args", "skill_id is required")
    out = fetch_skill_body(
        _runtime.db(),
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
    report = impact_check(
        _runtime.db(),
        workspace_id=workspace_id,
        file_path=file_path,
        callers_limit=callers_limit,
        hot_threshold=hot_threshold,
    )
    return _ok(report.to_dict())


# ============================================================
# Dispatch table — name → handler
# ============================================================


V3_HANDLERS: dict[str, Any] = {
    "memory_search": _handle_v3_search,
    "memory_get": _handle_v3_get,
    "memory_write": _handle_v3_write,
    "memory_edit": _handle_v3_edit,
    "memory_pin": _handle_v3_pin,
    "memory_archive": _handle_v3_archive,
    "memory_brief": _handle_v3_brief,
    "memory_lint": _handle_v3_lint,
    "memory_invoke_skill": _handle_v3_invoke_skill,
    "memory_impact_check": _handle_v3_impact_check,
}
