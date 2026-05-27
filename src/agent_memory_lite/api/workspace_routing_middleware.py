"""ASGI middleware: route ``/memory/*`` calls to the workspace's own DB.

When the HTTP service runs in hub mode any direct caller can pass a
``workspace_id`` query parameter or JSON body field and expect the call
to land on that workspace's registered SQLite + LanceDB pair. The MCP
stdio server already does this by injecting ``X-Memory-DB-Path`` /
``X-Memory-Vector-Path`` headers per call, but ``curl``, the local UI,
and the CLI tools historically did not — so they silently hit the
service's anchor DB.

This middleware closes that gap. For every ``/memory/*`` request it:

* Skips routing entirely when an explicit ``X-Memory-DB-Path`` header
  is already set (the caller has chosen) or when ``hub_mode`` is off
  (project-mode anchors are authoritative).
* Reads ``workspace_id`` from the query string for any HTTP method, and
  from a JSON body for methods that carry one (``POST``, ``PUT``,
  ``PATCH``, ``DELETE``). Body bytes are buffered once so downstream
  handlers re-read the same payload without observing an empty stream.
* Resolves the value against the hub registry via
  ``resolve_workspace_paths``. On a hit, the resolved paths are
  injected as headers in the ASGI scope so the existing
  ``_resolve_db_path`` / ``get_vector_store_dep`` plumbing in
  ``api/deps.py`` picks them up unchanged.

The middleware is purely additive: a request that does not name a
workspace, or names one that is not registered, behaves exactly as
before.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent_memory_lite.api.workspace_routing import (
    ResolvedWorkspacePaths,
    resolve_workspace_paths,
)
from agent_memory_lite.config.settings import Settings

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DB_HEADER = b"x-memory-db-path"
_VECTOR_HEADER = b"x-memory-vector-path"


class WorkspaceRoutingMiddleware:
    """Inject per-workspace path headers when hub mode is on."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self._app = app
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_target_request(scope):
            await self._app(scope, receive, send)
            return

        # Extract workspace_id from query first (cheap, no body read).
        workspace_id = _query_workspace_id(scope)
        body_bytes: bytes | None = None

        # Fall back to body for methods that carry one and only when the
        # query did not already pin a workspace.
        if not workspace_id and scope.get("method", "").upper() in _BODY_METHODS:
            body_bytes = await _drain_body(receive)
            workspace_id = _body_workspace_id(scope, body_bytes)
            receive = _replay_body_receive(body_bytes)

        resolved = resolve_workspace_paths(workspace_id, self._settings)
        if resolved is not None:
            scope = _scope_with_routing_headers(scope, resolved)

        await self._app(scope, receive, send)

    def _is_target_request(self, scope: Scope) -> bool:
        if scope.get("type") != "http":
            return False
        if not self._settings.hub_mode:
            return False
        path = scope.get("path", "")
        # Route every /memory/* request in hub mode. Canonical compact
        # endpoints (/memory/brief, /memory/search, /memory/write ...)
        # share the same prefix post 2026-05-18 rename. The middleware reads
        # workspace_id from query or JSON body and injects per-
        # workspace DB / vector paths so the call lands on the right
        # SQLite + LanceDB pair instead of the anchor DB.
        if not path.startswith("/memory/"):
            return False
        # Don't override an explicit caller choice.
        return all(name != _DB_HEADER for name, _ in scope.get("headers", []))


def _query_workspace_id(scope: Scope) -> str | None:
    raw = scope.get("query_string", b"")
    if not raw:
        return None
    try:
        params = parse_qs(raw.decode("latin-1"), keep_blank_values=False)
    except Exception:
        return None
    values = params.get("workspace_id")
    if not values:
        return None
    return values[0] or None


def _body_workspace_id(scope: Scope, body: bytes) -> str | None:
    if not body:
        return None
    if not _has_json_content_type(scope):
        return None
    try:
        payload: Any = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict):
        value = payload.get("workspace_id")
        if isinstance(value, str) and value:
            return value
    return None


def _has_json_content_type(scope: Scope) -> bool:
    for name, value in scope.get("headers", []):
        if name == b"content-type":
            decoded = value.decode("latin-1").lower()
            return "json" in decoded
    return False


async def _drain_body(receive: Receive) -> bytes:
    """Buffer the entire request body so we can both inspect and replay it."""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(message.get("body", b"") or b"")
            if not message.get("more_body", False):
                break
        elif message["type"] == "http.disconnect":
            break
    return b"".join(chunks)


def _replay_body_receive(body: bytes) -> Receive:
    """Build a one-shot receive callable that re-emits the buffered body."""
    sent = False

    async def _receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return _receive


def _scope_with_routing_headers(scope: Scope, resolved: ResolvedWorkspacePaths) -> Scope:
    """Return a shallow copy of scope with the routing headers appended."""
    headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
    headers = _set_header(headers, _DB_HEADER, resolved.db_path.encode("latin-1"))
    headers = _set_header(headers, _VECTOR_HEADER, resolved.vector_path.encode("latin-1"))
    new_scope: dict[str, Any] = dict(scope)
    new_scope["headers"] = headers
    return new_scope


def _set_header(
    headers: list[tuple[bytes, bytes]], name: bytes, value: bytes
) -> list[tuple[bytes, bytes]]:
    filtered = [(n, v) for (n, v) in headers if n != name]
    filtered.append((name, value))
    return filtered


__all__ = ["WorkspaceRoutingMiddleware"]


# Re-export type for external callers that want to plug in custom hooks.
_ = Callable[[Scope, Receive, Send], Awaitable[None]]
