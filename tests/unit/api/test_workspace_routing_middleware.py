"""Unit tests for WorkspaceRoutingMiddleware._is_target_request.

Regression coverage for the 2026-05-18 fix: the middleware originally
matched only ``/memory/*`` paths, so any ``/memory/*`` request
slipped past the routing layer and silently landed on the service's
anchor DB regardless of ``workspace_id``. In hub mode this made the v3
brief endpoint unusable cross-workspace — copyBot's brief always
returned agent-memory-lite's data, hiding the v3 pinned rules from
the copyBot agent and dropping v3 adoption to 0%.

These tests pin the contract: both legacy ``/memory/*`` and v3
``/memory/*`` paths are routing targets when hub mode is on.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_memory_lite.api.workspace_routing_middleware import WorkspaceRoutingMiddleware


@dataclass
class _FakeSettings:
    """Stand-in for ``Settings`` — only ``hub_mode`` is read."""

    hub_mode: bool


def _scope(method: str, path: str, *, db_header: bool = False) -> dict:
    """Build a minimal ASGI scope dict suitable for _is_target_request."""
    headers: list[tuple[bytes, bytes]] = []
    if db_header:
        headers.append((b"x-memory-db-path", b"/explicit/db"))
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers,
    }


def _make_middleware(*, hub_mode: bool) -> WorkspaceRoutingMiddleware:
    return WorkspaceRoutingMiddleware(app=None, settings=_FakeSettings(hub_mode=hub_mode))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "path",
    [
        "/memory/get_context",
        "/memory/list_decisions",
        "/memory/hygiene_report",
    ],
)
def test_routing_matches_legacy_memory_paths(path: str) -> None:
    """Legacy /memory/* paths are routing targets in hub mode."""
    mw = _make_middleware(hub_mode=True)
    assert mw._is_target_request(_scope("GET", path)) is True


@pytest.mark.parametrize(
    "path",
    [
        "/memory/brief",
        "/memory/search",
        "/memory/impact_check",
        "/memory/get",
        "/memory/write",
        "/memory/lint",
    ],
)
def test_routing_matches_v3_memory_paths(path: str) -> None:
    """v3 /memory/* paths are routing targets in hub mode.

    Before the 2026-05-18 fix this returned False and v3 requests
    silently hit the anchor DB regardless of workspace_id.
    """
    mw = _make_middleware(hub_mode=True)
    assert mw._is_target_request(_scope("GET", path)) is True


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/openapi.json",
        "/ui",
        "/ui/code",
        "/memoryctl/foo",  # similar prefix but not /memory/
        "/v3/something_else",
        "/v3memory/brief",
    ],
)
def test_routing_skips_non_memory_paths(path: str) -> None:
    """Unrelated paths must NOT be routed (no body draining, no headers)."""
    mw = _make_middleware(hub_mode=True)
    assert mw._is_target_request(_scope("GET", path)) is False


@pytest.mark.parametrize(
    "path",
    [
        "/memory/get_context",
        "/memory/brief",
    ],
)
def test_routing_disabled_when_hub_mode_off(path: str) -> None:
    """In project mode the middleware is a no-op — anchor DB is authoritative."""
    mw = _make_middleware(hub_mode=False)
    assert mw._is_target_request(_scope("GET", path)) is False


@pytest.mark.parametrize(
    "path",
    [
        "/memory/get_context",
        "/memory/brief",
    ],
)
def test_routing_skipped_when_caller_set_explicit_db_header(path: str) -> None:
    """An explicit X-Memory-DB-Path header means the caller has chosen — no override."""
    mw = _make_middleware(hub_mode=True)
    assert mw._is_target_request(_scope("GET", path, db_header=True)) is False


def test_routing_skips_non_http_scopes() -> None:
    """Websocket / lifespan scopes are not targets."""
    mw = _make_middleware(hub_mode=True)
    ws_scope = {"type": "websocket", "method": "GET", "path": "/memory/brief"}
    assert mw._is_target_request(ws_scope) is False  # type: ignore[arg-type]
    lifespan_scope = {"type": "lifespan"}
    assert mw._is_target_request(lifespan_scope) is False  # type: ignore[arg-type]
