from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent_memory_lite.mcp import (
    stdio_handlers_capabilities,
    stdio_handlers_decisions,
    stdio_handlers_episodes,
    stdio_server,
)


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

    monkeypatch.setattr(stdio_handlers_episodes, "_http_get_context", fake_http)
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

    class _StubConn:
        """Minimal conn shim with the operations apply_post_build_hooks may
        run. The local fallback path now calls the same post-build hook
        chain the HTTP route uses, so the conn must support PRAGMA + the
        pending_review SELECT COUNT queries."""

        def execute(self, sql: str, *_: object) -> object:
            class _Cursor:
                def fetchone(self) -> tuple[int, ...]:
                    return (0,)

                def fetchall(self) -> list[object]:
                    return []

                def __iter__(self):  # type: ignore[no-untyped-def]
                    return iter(())

            return _Cursor()

        def commit(self) -> None:
            return None

    stub_conn = _StubConn()

    def fake_build_context(
        conn: object,
        query: object,
        *,
        embedding_provider: object | None,
        vector_store: object | None,
    ) -> SimpleNamespace:
        assert conn is stub_conn
        assert embedding_provider is None
        assert vector_store is None
        assert query.workspace_id == "copyBot"
        return SimpleNamespace(
            text="<memory_context><retrieved_chunks/></memory_context>",
            hits=[SimpleNamespace(id="chk_1", score=1.0, sources=["fts"], path="")],
            decisions=[],
            theories=[],
            behavior_instructions=None,
        )

    monkeypatch.setattr(stdio_handlers_episodes, "_http_get_context", lambda _payload: None)
    monkeypatch.setattr(stdio_server._runtime, "db", lambda: stub_conn)
    monkeypatch.setattr(stdio_server._runtime, "db_for", lambda _ws: stub_conn)
    monkeypatch.setattr(stdio_server._runtime, "provider", fail_provider)
    monkeypatch.setattr(stdio_server._runtime, "store", fail_store)
    monkeypatch.setattr(stdio_handlers_episodes, "build_context", fake_build_context)

    result = stdio_server._handle_get_context(
        {"workspace_id": "copyBot", "query": "Surgical changes only", "max_tokens": 200}
    )

    assert result == {
        "context_text": "<memory_context><retrieved_chunks/></memory_context>",
        "sources": [{"id": "chk_1", "score": 1.0, "sources": ["fts"], "path": ""}],
    }


def test_mcp_local_fallback_runs_post_build_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When HTTP is down and MCP runs the local fallback, it must still
    invoke ``apply_post_build_hooks`` so v1.5/v1.6/v2.2/v2.3 fire on
    MCP-only deployments. Pre-fix the local fallback called
    ``build_context`` directly and silently skipped the hook chain.
    """
    captured: list[dict] = []

    def fake_apply_hooks(conn, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(
            {
                "request_workspace_id": kwargs["request_workspace_id"],
                "envelope_text": kwargs["envelope_text"],
                "embedding_provider": kwargs.get("embedding_provider"),
                "vector_store": kwargs.get("vector_store"),
            }
        )
        return kwargs["envelope_text"] + "\n<pending_review/>"

    def fake_build_context(conn, query, *, embedding_provider, vector_store):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            text="<memory_context><retrieved_chunks/></memory_context>",
            hits=[SimpleNamespace(id="chk_1", score=1.0, sources=["fts"], path="")],
            decisions=[],
            theories=[],
            behavior_instructions=None,
        )

    monkeypatch.setattr(stdio_handlers_episodes, "_http_get_context", lambda _payload: None)
    monkeypatch.setattr(stdio_server._runtime, "db_for", lambda _ws: object())
    monkeypatch.setattr(stdio_handlers_episodes, "build_context", fake_build_context)
    monkeypatch.setattr(stdio_handlers_episodes, "apply_post_build_hooks", fake_apply_hooks)

    result = stdio_server._handle_get_context(
        {"workspace_id": "copyBot", "query": "Verify hook chain"}
    )

    assert len(captured) == 1, "MCP local fallback bypassed apply_post_build_hooks"
    assert captured[0]["request_workspace_id"] == "copyBot"
    # MCP fallback intentionally does NOT load embedding model / vector store
    # for the hook chain — sentinels run FTS-only when needed.
    assert captured[0]["embedding_provider"] is None
    assert captured[0]["vector_store"] is None
    # Final envelope must include the hook's contribution.
    assert "<pending_review/>" in result["context_text"]


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

    monkeypatch.setattr(stdio_handlers_episodes, "_http_search", fake_http)
    monkeypatch.setattr(stdio_handlers_episodes, "search_chunks_fts", fail_local_search)

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
                is_archived=False,
            )
        ]

    monkeypatch.setattr(stdio_handlers_episodes, "_http_search", lambda _payload: None)
    monkeypatch.setattr(stdio_server._runtime, "db", lambda: "db")
    monkeypatch.setattr(stdio_handlers_episodes, "search_chunks_fts", fake_local_search)

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

    monkeypatch.setattr(stdio_handlers_episodes, "_http_ingest_episode", fake_http)
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

    monkeypatch.setattr(stdio_handlers_episodes, "_http_ingest_file", fake_http)
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

    monkeypatch.setattr(stdio_handlers_decisions, "_http_write", fake_http)
    monkeypatch.setattr(stdio_handlers_decisions, "write_decision", fail_local_write)

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

    monkeypatch.setattr(stdio_handlers_capabilities, "_http_write", fake_http)
    monkeypatch.setattr(stdio_handlers_capabilities, "upsert_agent_role", fail_local_upsert)

    result = stdio_server._handle_upsert_agent_role(
        {
            "workspace_id": "copyBot",
            "name": "Live observer role",
            "purpose": "Verify telemetry graph behavior.",
        }
    )

    assert result["role_id"] == "role_1"
