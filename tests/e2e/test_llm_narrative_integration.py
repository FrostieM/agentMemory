"""2.1.3: e2e for LLM-driven file digest narrative.

Locks two contracts:
1. Flag-OFF parity — narrative matches the heuristic baseline,
   ``structured.narrative_source == "heuristic"``.
2. Flag-ON with mocked Ollama — narrative replaced by canned LLM
   text, ``structured.narrative_source == "llm"``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.repositories.file_digests_repo import get_digest


@pytest.fixture
def client_default(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="narr-off-ws")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_llm_on(app_factory) -> Iterator[TestClient]:
    app = app_factory(
        MEMORY_WORKSPACE_ID="narr-on-ws",
        MEMORY_LLM_NARRATIVE_ENABLED="true",
        MEMORY_LLM_NARRATIVE_MIN_SYMBOLS="1",
    )
    with TestClient(app) as c:
        yield c


_PY_SRC = "def alpha(): pass\n\nclass Beta:\n    def gamma(self): pass\n"


def _ingest(client: TestClient, ws: str, path: str, content: str) -> dict:
    r = client.post(
        "/memory/ingest_file",
        json={
            "workspace_id": ws,
            "path": path,
            "content": content,
            "language": "python",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _stored_digest(tmp_db_path: Path, *, workspace_id: str, file_path: str):
    conn = open_connection(tmp_db_path)
    try:
        digest = get_digest(conn, workspace_id=workspace_id, file_path=file_path)
    finally:
        close_connection(conn)
    assert digest is not None
    return digest


def test_flag_off_uses_heuristic_narrative(client_default: TestClient, tmp_db_path: Path) -> None:
    _ingest(client_default, "narr-off-ws", "src/a.py", _PY_SRC)
    digest = _stored_digest(tmp_db_path, workspace_id="narr-off-ws", file_path="src/a.py")
    assert digest.structured["narrative_source"] == "heuristic"
    # Heuristic narrative mentions the path.
    assert "src/a.py" in digest.narrative


def test_flag_on_uses_llm_narrative(client_llm_on: TestClient, tmp_db_path: Path) -> None:
    """Flag on + mocked Ollama returning canned text → narrative
    replaced with that text, source flagged as 'llm'."""

    class _MockResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "message": {
                    "content": "Module alpha-beta wires the user-flow handler.",
                }
            }

    class _MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            return None

        def post(self, *args, **kwargs):
            return _MockResponse()

    with patch(
        "agent_memory_lite.extraction.file_narrative_llm.httpx.Client",
        _MockClient,
    ):
        _ingest(client_llm_on, "narr-on-ws", "src/a.py", _PY_SRC)
    digest = _stored_digest(tmp_db_path, workspace_id="narr-on-ws", file_path="src/a.py")
    assert digest.structured["narrative_source"] == "llm"
    assert "user-flow handler" in digest.narrative


def test_flag_on_falls_back_when_ollama_unreachable(
    client_llm_on: TestClient, tmp_db_path: Path
) -> None:
    """Flag on but Ollama unreachable → heuristic still used.
    Real network call would time out; we patch httpx to raise."""

    class _RaisingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            return None

        def post(self, *args, **kwargs):
            raise httpx.ConnectError("refused")

    with patch(
        "agent_memory_lite.extraction.file_narrative_llm.httpx.Client",
        _RaisingClient,
    ):
        _ingest(client_llm_on, "narr-on-ws", "src/b.py", _PY_SRC)
    digest = _stored_digest(tmp_db_path, workspace_id="narr-on-ws", file_path="src/b.py")
    # Network failure → heuristic baseline survives.
    assert digest.structured["narrative_source"] == "heuristic"
