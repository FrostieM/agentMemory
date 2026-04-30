from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent_memory_lite.mcp import stdio_server


def test_get_context_uses_http_delegation_without_loading_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_provider() -> None:
        raise AssertionError("provider should not load when HTTP delegation succeeds")

    def fail_store() -> None:
        raise AssertionError("vector store should not load when HTTP delegation succeeds")

    def fake_http(payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["workspace_id"] == "copyBot"
        assert payload["query"] == "Surgical changes only"
        return {"context_text": "<memory_context/>", "sources": []}

    monkeypatch.setattr(stdio_server, "_http_get_context", fake_http)
    monkeypatch.setattr(stdio_server._runtime, "provider", fail_provider)
    monkeypatch.setattr(stdio_server._runtime, "store", fail_store)

    result = stdio_server._handle_get_context(
        {"workspace_id": "copyBot", "query": "Surgical changes only"}
    )

    assert result == {"context_text": "<memory_context/>", "sources": []}


def test_get_context_falls_back_to_fts_only_without_loading_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_provider() -> None:
        raise AssertionError("provider should not load for local fallback")

    def fail_store() -> None:
        raise AssertionError("vector store should not load for local fallback")

    def fake_build_context(
        conn: object,
        query: object,
        *,
        embedding_provider: object | None,
        vector_store: object | None,
    ) -> SimpleNamespace:
        assert conn == "db"
        assert embedding_provider is None
        assert vector_store is None
        assert query.workspace_id == "copyBot"
        return SimpleNamespace(
            text="<memory_context><retrieved_chunks/></memory_context>",
            hits=[SimpleNamespace(id="chk_1", score=1.0, sources=["fts"], path="")],
        )

    monkeypatch.setattr(stdio_server, "_http_get_context", lambda _payload: None)
    monkeypatch.setattr(stdio_server._runtime, "db", lambda: "db")
    monkeypatch.setattr(stdio_server._runtime, "provider", fail_provider)
    monkeypatch.setattr(stdio_server._runtime, "store", fail_store)
    monkeypatch.setattr(stdio_server, "build_context", fake_build_context)

    result = stdio_server._handle_get_context(
        {"workspace_id": "copyBot", "query": "Surgical changes only", "max_tokens": 200}
    )

    assert result == {
        "context_text": "<memory_context><retrieved_chunks/></memory_context>",
        "sources": [{"id": "chk_1", "score": 1.0, "sources": ["fts"], "path": ""}],
    }


def test_search_uses_http_delegation_for_live_ui_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_local_search(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("local FTS should not run when HTTP search delegation succeeds")

    def fake_http(payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["workspace_id"] == "copyBot"
        assert payload["query"] == "live observer"
        assert payload["mode"] == "fts"
        return {
            "mode": "fts",
            "hits": [
                {
                    "chunk_id": "chk_1",
                    "score": -1.0,
                    "path": "",
                    "text": "live observer",
                    "summary": None,
                }
            ],
        }

    monkeypatch.setattr(stdio_server, "_http_search", fake_http)
    monkeypatch.setattr(stdio_server, "search_chunks_fts", fail_local_search)

    result = stdio_server._handle_search(
        {"workspace_id": "copyBot", "query": "live observer", "limit": 5}
    )

    assert result["hits"][0]["chunk_id"] == "chk_1"


def test_search_falls_back_to_local_fts_when_http_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_local_search(*_args: object, **kwargs: object) -> list[SimpleNamespace]:
        assert kwargs["workspace_id"] == "copyBot"
        assert kwargs["query"] == "fallback observer"
        return [
            SimpleNamespace(
                chunk_id="chk_local",
                score=-2.0,
                path="",
                text="fallback observer",
                summary=None,
            )
        ]

    monkeypatch.setattr(stdio_server, "_http_search", lambda _payload: None)
    monkeypatch.setattr(stdio_server._runtime, "db", lambda: "db")
    monkeypatch.setattr(stdio_server, "search_chunks_fts", fake_local_search)

    result = stdio_server._handle_search(
        {"workspace_id": "copyBot", "query": "fallback observer", "limit": 5}
    )

    assert result["hits"][0]["chunk_id"] == "chk_local"


def test_ingest_episode_uses_http_delegation_without_loading_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_provider() -> None:
        raise AssertionError("provider should not load when HTTP ingestion succeeds")

    def fail_store() -> None:
        raise AssertionError("vector store should not load when HTTP ingestion succeeds")

    def fake_http(payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["workspace_id"] == "copyBot"
        assert payload["source_type"] == "agent_action"
        assert payload["trust_level"] == "agent_observed"
        return {
            "episode_id": "ep_1",
            "chunk_id": "chk_1",
            "redacted_text": payload["raw_text"],
            "redacted_kinds": [],
            "created_at": "2026-01-01T00:00:00Z",
            "embedded": True,
            "auto_promoted_decisions": 0,
            "auto_promoted_rules": 0,
            "auto_promoted_core": 0,
            "candidates_written": 0,
        }

    monkeypatch.setattr(stdio_server, "_http_ingest_episode", fake_http)
    monkeypatch.setattr(stdio_server._runtime, "provider", fail_provider)
    monkeypatch.setattr(stdio_server._runtime, "store", fail_store)

    result = stdio_server._handle_ingest_episode(
        {"workspace_id": "copyBot", "raw_text": "MCP write delegation smoke"}
    )

    assert result["episode_id"] == "ep_1"
    assert result["embedded"] is True


def test_ingest_file_uses_http_delegation_without_loading_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_provider() -> None:
        raise AssertionError("provider should not load when HTTP file ingestion succeeds")

    def fail_store() -> None:
        raise AssertionError("vector store should not load when HTTP file ingestion succeeds")

    def fake_http(payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["workspace_id"] == "copyBot"
        assert payload["path"] == "docs/contract.md"
        return {
            "file_id": "file_1",
            "path": payload["path"],
            "chunks_written": 1,
            "skipped": False,
            "last_indexed_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(stdio_server, "_http_ingest_file", fake_http)
    monkeypatch.setattr(stdio_server._runtime, "provider", fail_provider)
    monkeypatch.setattr(stdio_server._runtime, "store", fail_store)

    result = stdio_server._handle_ingest_file(
        {"workspace_id": "copyBot", "path": "docs/contract.md", "content": "contract"}
    )

    assert result["file_id"] == "file_1"
    assert result["chunks_written"] == 1


def test_write_decision_uses_http_delegation_for_live_ui_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_local_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("local decision write should not run when HTTP delegation succeeds")

    def fake_http(path: str, payload: dict[str, Any], *, log_label: str) -> dict[str, Any]:
        assert path == "/memory/write_decision"
        assert log_label == "write_decision"
        assert payload["workspace_id"] == "copyBot"
        assert payload["title"] == "Observer bridge"
        return {
            "decision_id": "dec_1",
            "status": "active",
            "valid_from": "2026-01-01T00:00:00Z",
            "superseded_decision_id": None,
        }

    monkeypatch.setattr(stdio_server, "_http_write", fake_http)
    monkeypatch.setattr(stdio_server, "write_decision", fail_local_write)

    result = stdio_server._handle_write_decision(
        {
            "workspace_id": "copyBot",
            "title": "Observer bridge",
            "decision_text": "Route MCP writes through HTTP telemetry.",
        }
    )

    assert result["decision_id"] == "dec_1"


def test_upsert_agent_role_uses_http_delegation_for_graph_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_local_upsert(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("local role upsert should not run when HTTP delegation succeeds")

    def fake_http(path: str, payload: dict[str, Any], *, log_label: str) -> dict[str, Any]:
        assert path == "/memory/upsert_agent_role"
        assert log_label == "upsert_agent_role"
        assert payload["workspace_id"] == "copyBot"
        assert payload["name"] == "Live observer role"
        return {
            "role_id": "role_1",
            "name": "Live observer role",
            "confidence": 0.9,
            "active": True,
            "updated_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(stdio_server, "_http_write", fake_http)
    monkeypatch.setattr(stdio_server, "upsert_agent_role", fail_local_upsert)

    result = stdio_server._handle_upsert_agent_role(
        {
            "workspace_id": "copyBot",
            "name": "Live observer role",
            "purpose": "Verify telemetry graph behavior.",
        }
    )

    assert result["role_id"] == "role_1"
