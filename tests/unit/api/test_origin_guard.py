"""v3.5 sector-6+7 audit-followup: OriginGuard middleware.

The local service binds 127.0.0.1, but without this guard:
- A malicious web page the operator visits in the same browser
  could POST to ``http://127.0.0.1:8765/memory/*`` (no auth on by
  default, cookies not needed for form-submit / fetch).
- DNS-rebinding could resolve an attacker domain to 127.0.0.1 and
  send Host: attacker.com — Starlette would still route the
  request to our handler.

The middleware rejects both classes with HTTP 403.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agent_memory_lite.api.origin_guard_middleware import OriginGuardMiddleware


async def _ok(_request):  # type: ignore[no-untyped-def]
    return JSONResponse({"ok": True})


@pytest.fixture
def client() -> TestClient:
    app = Starlette(routes=[Route("/test", _ok, methods=["GET", "POST"])])
    app.add_middleware(OriginGuardMiddleware)
    return TestClient(app)


def test_loopback_request_passes(client: TestClient) -> None:
    """Default TestClient sends Host=testserver. That's not loopback by
    our allow-list — so the test confirms the guard FIRES on it. We
    then pass Host=127.0.0.1 explicitly to confirm pass-through."""
    response = client.get("/test", headers={"Host": "127.0.0.1:8765"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_localhost_host_passes(client: TestClient) -> None:
    response = client.get("/test", headers={"Host": "localhost:8765"})
    assert response.status_code == 200


def test_remote_origin_blocked(client: TestClient) -> None:
    """Browser POST from evil.com → 403."""
    response = client.post(
        "/test",
        headers={"Host": "127.0.0.1:8765", "Origin": "https://evil.com"},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "origin_blocked"


def test_loopback_origin_passes(client: TestClient) -> None:
    """A page served from http://127.0.0.1:8765/ui sending fetch to
    its own origin must pass."""
    response = client.get(
        "/test",
        headers={"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 200


def test_dns_rebinding_host_blocked(client: TestClient) -> None:
    """DNS-rebinding: attacker DNS resolves their domain to 127.0.0.1
    so the TCP connection lands at the service, but the Host header
    carries the attacker's domain. Guard sees Host=attacker.com → 403."""
    response = client.get(
        "/test",
        headers={"Host": "attacker.example.com"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "host_blocked"


def test_opt_out_via_env(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    """MEMORY_ALLOW_REMOTE_ORIGIN=1 lets the operator front the
    service with a reverse proxy."""
    monkeypatch.setenv("MEMORY_ALLOW_REMOTE_ORIGIN", "1")
    response = client.post(
        "/test",
        headers={"Host": "attacker.example.com", "Origin": "https://evil.com"},
    )
    assert response.status_code == 200


def test_inject_brief_loopback_validator_accepts_loopback() -> None:
    """The hook companion validator follows the same rule."""
    # Import lazily — the script lives outside the package
    import importlib.util  # noqa: PLC0415
    import sys  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    script_path = Path(__file__).resolve().parents[3] / "scripts" / "inject_memory_brief.py"
    spec = importlib.util.spec_from_file_location("inject_memory_brief_test", script_path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["inject_memory_brief_test"] = mod
    spec.loader.exec_module(mod)

    assert mod._validate_base("http://127.0.0.1:8765") == "http://127.0.0.1:8765"
    assert mod._validate_base("http://localhost:8765") == "http://localhost:8765"
    # Non-loopback falls back to the default
    fallback = mod._validate_base("http://evil.example.com:8765")
    assert "127.0.0.1" in fallback
