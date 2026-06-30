"""H4 (global-audit 2026-06-30): the app's middleware registration order.

Starlette runs the LAST-added middleware OUTERMOST (first on the inbound path),
and ``add_middleware`` inserts at index 0 of ``app.user_middleware`` -- so a LOWER
index is MORE OUTER. The previous code registered top-to-bottom as if the first
call were outermost, which inverted every layer; most critically WorkspaceRouting
(which buffers the whole request body to read workspace_id) ran OUTSIDE
BodySizeLimit, so it drained an unbounded body before the DoS cap applied. These
tests pin the corrected order so that regression cannot silently return.
"""

from __future__ import annotations

from agent_memory_lite.api.app import create_app
from agent_memory_lite.api.body_size_limit_middleware import BodySizeLimitMiddleware
from agent_memory_lite.api.security_middleware import SecurityMiddleware
from agent_memory_lite.api.workspace_routing_middleware import WorkspaceRoutingMiddleware


def _outermost_first(settings_factory) -> list[type]:
    """Middleware classes in OUTERMOST-first (inbound) order."""
    app = create_app(settings_factory())
    # app.user_middleware[0] is the last-added == outermost; the list is already
    # in outermost-first order.
    return [m.cls for m in app.user_middleware]


def test_body_cap_is_outer_than_workspace_routing(settings_factory) -> None:
    order = _outermost_first(settings_factory)
    # Lower index == more outer == runs earlier inbound. BodySizeLimit must cap the
    # body BEFORE WorkspaceRouting drains it.
    assert order.index(BodySizeLimitMiddleware) < order.index(WorkspaceRoutingMiddleware)


def test_security_middleware_is_outermost(settings_factory) -> None:
    # Registered last so its CSP / X-Frame headers wrap every inner response,
    # including the 413 / 403 / 415 short-circuits below it.
    order = _outermost_first(settings_factory)
    assert order[0] is SecurityMiddleware
