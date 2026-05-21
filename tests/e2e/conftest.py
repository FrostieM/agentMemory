"""Shared fixtures for the e2e HTTP-route tests.

Every e2e test drives the app through ``fastapi.testclient.TestClient``,
which defaults to ``Host: testserver``. ``OriginGuardMiddleware`` (added
in v3.5) rejects any non-loopback Host as a DNS-rebinding risk, so a bare
``TestClient(app)`` 403s before the request reaches the route. These
tests exercise the ROUTES, not the origin guard (which has its own
dedicated middleware test), so the autouse fixture below pins the
TestClient default ``base_url`` to loopback. A test that deliberately
wants a foreign Host can still pass ``base_url`` explicitly —
``setdefault`` never overrides an explicit value.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_ORIGINAL_TESTCLIENT_INIT = TestClient.__init__


@pytest.fixture(autouse=True)
def _loopback_testclient_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every e2e ``TestClient`` to a loopback Host header.

    ``monkeypatch`` reverts the patch when the test finishes, so the
    fixture needs no explicit teardown.
    """

    def _init(self: TestClient, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("base_url", "http://127.0.0.1")
        _ORIGINAL_TESTCLIENT_INIT(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(TestClient, "__init__", _init)
