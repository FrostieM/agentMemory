"""Request-body size guard (v3.6 Round-2 audit).

The HTTP service binds 127.0.0.1 and (by default) has no auth — yet
the OriginGuard, DNS-rebinding guard, and Origin/Host check above us
only protect against cross-origin abuse. A local attacker (or a
careless tool loop) can still POST arbitrarily large bodies to
``/memory/ingest_episode`` / ``/memory/ingest_file`` and either:

1. Pin the embedding worker for minutes on a multi-MB transcript.
2. Bloat the SQLite WAL faster than checkpoint can recover.
3. Force pydantic to allocate gigabytes during validation.

The middleware enforces a hard cap (default 10 MB, env override) at
the ASGI boundary so the body never reaches a route handler:

* **Fast path** — reject on ``Content-Length`` header alone. Single
  integer compare; no body read.
* **Defense-in-depth** — when the client omits ``Content-Length``
  (HTTP/1.1 ``Transfer-Encoding: chunked``) we wrap the ASGI
  ``receive`` callable and tally bytes as they arrive. The first
  chunk that pushes us past the limit poisons the stream so the
  route handler gets an empty body (pydantic then returns 422).
  Not as graceful as a 413 but bounded — the attacker still can't
  exhaust memory.

Pure ASGI middleware (not BaseHTTPMiddleware) because we have to
intercept ``receive`` before any body bytes land in user code.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

# ASGI message types are loose dicts; type them narrowly here so the
# wrapped receive callable stays mypy-clean.
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
Scope = dict[str, Any]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int = 10_485_760) -> None:
        self.app = app
        self.max_bytes = max(1024, int(max_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # Header-fast-path: a well-behaved client always sends
        # Content-Length on POST/PUT. Reject before any body bytes
        # reach uvicorn's read buffer.
        headers = scope.get("headers") or []
        cl_raw: bytes | None = None
        for name, value in headers:
            if name == b"content-length":
                cl_raw = value
                break
        if cl_raw is not None:
            try:
                declared = int(cl_raw.decode("latin-1"))
            except (UnicodeDecodeError, ValueError):
                await _send_error(
                    send, 400, "invalid_content_length", "Content-Length header is not an integer"
                )
                return
            if declared > self.max_bytes:
                await _send_error(
                    send,
                    413,
                    "body_too_large",
                    f"Declared body size {declared} bytes exceeds limit of {self.max_bytes} bytes.",
                    extra={"max_bytes": self.max_bytes, "declared_bytes": declared},
                )
                return

        # Chunked / unknown-length path: count bytes as they arrive.
        # Once over the limit, swallow the rest of the stream and
        # signal end-of-body so the handler sees a truncated payload.
        # The handler's pydantic schema will then fail validation
        # (422) — bounded blast radius, no graceful 413 here because
        # ASGI doesn't let us send a response from inside `receive`
        # after the app already started reading.
        bytes_seen = 0
        max_bytes = self.max_bytes
        truncated = False

        async def limited_receive() -> Message:
            nonlocal bytes_seen, truncated
            if truncated:
                # Already over limit; pretend the stream ended cleanly.
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"") or b""
                bytes_seen += len(body)
                if bytes_seen > max_bytes:
                    truncated = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        await self.app(scope, limited_receive, send)


async def _send_error(
    send: Send,
    status: int,
    error: str,
    detail: str,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"error": error, "detail": detail}
    if extra:
        payload.update(extra)
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
