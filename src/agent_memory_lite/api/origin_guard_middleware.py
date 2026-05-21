"""Origin / Host guard middleware.

v3.5 sector-6+7 audit-followup: agent-memory-lite binds 127.0.0.1
only, but a malicious web page the operator visits in the SAME
browser can still POST to ``http://127.0.0.1:8765/memory/*`` via
DNS-rebinding or simple form submission (cookies aren't required —
the service has no auth by default). Any such page could trigger
``/memory/promote_candidate``, ``/memory/release_edit``,
``/memory/reflex/delete``, etc.

This middleware rejects requests whose ``Origin`` (browser-set) or
``Host`` (always present) header points at a host that is NOT
loopback. Curl / httpx / the inject hooks send Host=127.0.0.1 by
default so the legitimate paths keep working. The UI dashboard
served from the same origin passes the check automatically.

Operator opt-out: ``MEMORY_ALLOW_REMOTE_ORIGIN=1`` for the case
where someone fronts the service with a reverse proxy on purpose.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_LOOPBACK_HOSTS: frozenset[str] = frozenset(
    {"127.0.0.1", "localhost", "::1", ""}  # empty = no Host header (HTTP/2 path)
)


def _is_loopback(host: str | None) -> bool:
    if host is None:
        return True
    raw = host.split(":", 1)[0].strip().lower()
    return raw in _LOOPBACK_HOSTS


def _allow_remote() -> bool:
    return os.environ.get("MEMORY_ALLOW_REMOTE_ORIGIN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class OriginGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if _allow_remote():
            return await call_next(request)
        host_ok = _is_loopback(request.headers.get("host"))
        origin_raw = request.headers.get("origin")
        if origin_raw:
            try:
                origin_host = urlparse(origin_raw).hostname
            except (ValueError, TypeError):
                origin_host = None
            if not _is_loopback(origin_host):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "origin_blocked",
                        "detail": (
                            f"Origin {origin_raw!r} is not loopback. The service "
                            "binds 127.0.0.1; set MEMORY_ALLOW_REMOTE_ORIGIN=1 to "
                            "opt into remote-origin access."
                        ),
                    },
                )
        if not host_ok:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "host_blocked",
                    "detail": (
                        f"Host header {request.headers.get('host')!r} is not "
                        "loopback. DNS-rebinding mitigation."
                    ),
                },
            )
        return await call_next(request)
