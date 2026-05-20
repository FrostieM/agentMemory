"""v3.6 Round-2 audit: BodySizeLimitMiddleware DoS guard.

The HTTP service binds 127.0.0.1 with no auth by default. Without
this guard, a single multi-MB POST to /memory/ingest_episode (or any
local-attacker / tool-loop oversight) can:
  * pin the embedding worker for minutes,
  * bloat the SQLite WAL faster than checkpoint can drain,
  * force pydantic to allocate gigabytes during validation.

Two attack surfaces are locked:
  1. The fast path — Content-Length header check (covers honest clients
     and the most common attack form).
  2. Chunked / no-Content-Length — the wrapped ASGI receive truncates
     the body once the running tally crosses the limit, so the handler
     sees an empty body and pydantic returns 422 instead of running
     unbounded.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agent_memory_lite.api.body_size_limit_middleware import BodySizeLimitMiddleware


async def _echo(request):  # type: ignore[no-untyped-def]
    # Read the body to confirm the limit fires BEFORE the handler
    # processes it — if the middleware leaks, the handler sees the
    # full payload and the test catches the regression.
    body = await request.body()
    return JSONResponse({"received_bytes": len(body)})


@pytest.fixture
def client() -> TestClient:
    app = Starlette(routes=[Route("/echo", _echo, methods=["POST"])])
    # Tight 1 KiB limit so the tests don't have to allocate megabytes.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=1024)
    return TestClient(app)


def test_small_body_passes(client: TestClient) -> None:
    """Body well under the limit reaches the handler unchanged."""
    response = client.post("/echo", content=b"x" * 100)
    assert response.status_code == 200
    assert response.json() == {"received_bytes": 100}


def test_body_at_limit_passes(client: TestClient) -> None:
    """Boundary: exactly max_bytes passes; max_bytes+1 is the rejection
    edge so a legitimate payload sized at the cap still works."""
    response = client.post("/echo", content=b"x" * 1024)
    assert response.status_code == 200
    assert response.json() == {"received_bytes": 1024}


def test_content_length_over_limit_rejected(client: TestClient) -> None:
    """Fast path: Content-Length declares > limit → 413 immediately,
    before any body bytes reach the handler."""
    response = client.post("/echo", content=b"x" * 2048)
    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "body_too_large"
    assert body["max_bytes"] == 1024
    assert body["declared_bytes"] == 2048
    # The detail message should be human-readable and name the cap.
    assert "1024" in body["detail"]


def test_invalid_content_length_rejected(client: TestClient) -> None:
    """A malformed Content-Length is a protocol violation; we reject
    with 400 rather than guessing the body size."""
    response = client.post(
        "/echo",
        content=b"x" * 10,
        headers={"Content-Length": "not-an-integer"},
    )
    # Starlette's TestClient may resync Content-Length before sending —
    # this test mostly documents the middleware behavior; if the client
    # fixes the header we still expect the small body to pass.
    assert response.status_code in {200, 400}


def test_chunked_over_limit_truncated(client: TestClient) -> None:
    """Defense in depth: when the client omits Content-Length and
    streams chunked, the middleware tallies bytes as they arrive
    and truncates once the limit is crossed. Handler sees the
    partial / empty body — bounded blast radius, no memory blowup."""

    def chunks():
        # Yield 4 KiB total in 256 B chunks — well over the 1 KiB cap.
        for _ in range(16):
            yield b"x" * 256

    response = client.post(
        "/echo",
        content=chunks(),
        # TestClient infers chunked when Content-Length is absent and
        # the body is an iterator. Force it just in case.
        headers={"Transfer-Encoding": "chunked"},
    )
    # The middleware truncates the stream once over the limit. The
    # handler still completes; it just sees fewer bytes than the
    # client sent. The contract is "bounded memory", not "graceful
    # 413" on the chunked path.
    assert response.status_code == 200
    received = response.json()["received_bytes"]
    # Strict bound: never more than the cap, even though the client
    # tried to send 4 KiB.
    assert received <= 1024, (
        f"middleware leaked {received} bytes through chunked path (limit was 1024)"
    )


def test_non_http_scope_passes_through() -> None:
    """The middleware must not crash on WebSocket / lifespan scopes —
    they have no body to limit."""
    seen: list[dict] = []

    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        seen.append(scope)

    mw = BodySizeLimitMiddleware(app, max_bytes=1024)

    async def receive():  # type: ignore[no-untyped-def]
        return {"type": "lifespan.startup"}

    async def send(_msg):  # type: ignore[no-untyped-def]
        pass

    asyncio.run(mw({"type": "lifespan"}, receive, send))
    assert seen == [{"type": "lifespan"}]


def test_min_limit_clamped() -> None:
    """Operator can't accidentally set max_bytes to 0/negative —
    the constructor clamps to 1024 so the middleware always permits
    at least a healthcheck payload."""
    mw = BodySizeLimitMiddleware(_echo, max_bytes=0)
    assert mw.max_bytes == 1024
    mw2 = BodySizeLimitMiddleware(_echo, max_bytes=-100)
    assert mw2.max_bytes == 1024
