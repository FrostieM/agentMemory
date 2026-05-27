"""Sector 7 Round-2 adversarial audit regression locks.

The error-body-disclosure LOW finding was already mitigated: the global
exception handler returns a generic envelope, no stack/SQL.

This file locks the SecurityMiddleware regressions.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agent_memory_lite.api.security_middleware import SecurityMiddleware


async def _ok(_request):  # type: ignore[no-untyped-def]
    return JSONResponse({"ok": True})


@pytest.fixture
def client() -> TestClient:
    app = Starlette(
        routes=[
            Route("/memory/write", _ok, methods=["POST"]),
            Route("/ui", _ok, methods=["GET"]),
            Route("/health", _ok, methods=["GET"]),
        ]
    )
    app.add_middleware(SecurityMiddleware)
    return TestClient(app)


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
    response = client.get("/health")
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_post_memory_without_json_content_type_rejected(client: TestClient) -> None:
    response = client.post(
        "/memory/write",
        content=b"title=pwn&decision_text=x",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"


def test_post_memory_text_plain_rejected(client: TestClient) -> None:
    response = client.post(
        "/memory/write",
        content=b"hello",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415


def test_post_memory_with_json_passes(client: TestClient) -> None:
    response = client.post("/memory/write", json={"title": "real"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_post_memory_json_with_charset_param_passes(client: TestClient) -> None:
    response = client.post(
        "/memory/write",
        content=b'{"title": "real"}',
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert response.status_code == 200


def test_get_memory_not_csrf_checked(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
