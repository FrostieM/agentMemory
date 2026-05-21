"""Sector 7 Round-2 adversarial audit — regression locks.

A fresh adversarial agent audited the UI layer:
  MEDIUM — no CSRF defense by design on mutating /memory/* routes
  MEDIUM — no security headers (CSP / X-Frame-Options / nosniff)
  MEDIUM — graph.html status() error path interpolated into innerHTML

(The error-body-disclosure LOW finding was already mitigated: the
global exception handler returns a generic envelope, no stack/SQL.)

This file locks the SecurityMiddleware + the graph.html fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agent_memory_lite.api.security_middleware import SecurityMiddleware

REPO_ROOT = Path(__file__).resolve().parents[3]


async def _ok(_request):  # type: ignore[no-untyped-def]
    return JSONResponse({"ok": True})


@pytest.fixture
def client() -> TestClient:
    app = Starlette(
        routes=[
            Route("/memory/write_decision", _ok, methods=["POST"]),
            Route("/ui", _ok, methods=["GET"]),
            Route("/health", _ok, methods=["GET"]),
        ]
    )
    app.add_middleware(SecurityMiddleware)
    return TestClient(app)


# ---------- security headers on every response ----------


def test_security_headers_present_on_get(client: TestClient) -> None:
    response = client.get("/ui")
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_security_headers_present_on_health(client: TestClient) -> None:
    """Even /health carries the headers — they ride every response."""
    response = client.get("/health")
    assert response.headers.get("X-Frame-Options") == "DENY"


# ---------- CSRF: content-type guard on mutating /memory/* ----------


def test_post_memory_without_json_content_type_rejected(client: TestClient) -> None:
    """A browser <form> can only POST x-www-form-urlencoded / multipart
    / text-plain. Such a POST to /memory/* must be rejected with 415 —
    the by-design CSRF guard."""
    response = client.post(
        "/memory/write_decision",
        content=b"title=pwn&decision_text=x",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"


def test_post_memory_text_plain_rejected(client: TestClient) -> None:
    """text/plain is the other form-reachable content-type — also 415."""
    response = client.post(
        "/memory/write_decision",
        content=b"hello",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415


def test_post_memory_with_json_passes(client: TestClient) -> None:
    """A legitimate JSON POST (curl / httpx / hooks / MCP all send this)
    must pass through untouched."""
    response = client.post("/memory/write_decision", json={"title": "real"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_post_memory_json_with_charset_param_passes(client: TestClient) -> None:
    """Content-Type may carry a ;charset= parameter — the guard parses
    the media type, not the raw header, so this still passes."""
    response = client.post(
        "/memory/write_decision",
        content=b'{"title": "real"}',
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert response.status_code == 200


def test_get_memory_not_csrf_checked(client: TestClient) -> None:
    """The CSRF guard is POST-only — a GET is not form-mutating and must
    not be blocked (and still carries the security headers)."""
    # /health is GET; confirm GET is never 415'd by the content-type guard.
    response = client.get("/health")
    assert response.status_code == 200


# ---------- graph.html status() XSS hardening ----------


def test_graph_html_status_uses_textcontent_for_errors() -> None:
    """graph.html status() must build the error span via textContent,
    not interpolate the message into an innerHTML template."""
    text = (REPO_ROOT / "src" / "agent_memory_lite" / "ui" / "graph.html").read_text(
        encoding="utf-8"
    )
    # The old vulnerable shape: `<span class="error">${msg}</span>` in innerHTML.
    assert '<span class="error">${msg}</span>' not in text, (
        "graph.html status() still interpolates msg into an innerHTML "
        "template — use textContent for the error span"
    )
    # The fixed shape: a textContent assignment for the error span.
    assert "span.textContent" in text, "graph.html status() error path must set span.textContent"
