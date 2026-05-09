"""Middleware that extracts X-Memory-Agent-Id and stashes in ContextVar.

Pairs with ``api/agent_context.py``. Mounted in ``api/app.py`` after
``WorkspaceRoutingMiddleware`` so the routing decision is made first,
then the agent identity is recorded for the audit-log writes that
follow.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from agent_memory_lite.api.agent_context import reset_current_agent_id, set_current_agent_id

_HEADER_NAME = "x-memory-agent-id"
_MAX_LEN = 64


class AgentIdentityMiddleware(BaseHTTPMiddleware):
    """Stash the request's agent identity in a ContextVar for the
    duration of the request. Header is optional; when absent the
    ContextVar stays ``None`` and audit rows are written without
    attribution (same as pre-1.3.0)."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        raw = request.headers.get(_HEADER_NAME)
        if raw is not None:
            cleaned = raw.strip()[:_MAX_LEN]
            set_current_agent_id(cleaned or None)
        else:
            set_current_agent_id(None)
        try:
            return await call_next(request)
        finally:
            reset_current_agent_id()
