"""v1.10: confirm CorrectionExtractor fires from the MCP stdio local
fallback path.

The MCP stdio handler ``_handle_ingest_episode`` first tries
``_http_ingest_episode`` (delegating to the HTTP service). When HTTP is
unreachable the handler falls back to a direct ``ingest_episode`` call
into the runtime's connection. That call must pass
``auto_promote_settings`` so the same ``auto_promote`` pipeline runs as
on the HTTP route — including the v1.10 ``CorrectionExtractor``. If
this contract drifts, MCP-only deployments lose the correction loop
entirely.

The full live-ingest path (in-memory SQLite + auto_promote +
CorrectionExtractor + memory_candidate write) is exercised by
``tests/integration/test_correction_loop_e2e.py`` against the FastAPI
TestClient. This unit test locks the MCP-side wire-up: the handler
forwards the runtime's settings and calls ingest_episode with them.
"""

from __future__ import annotations

import inspect
import sqlite3

import pytest

import agent_memory_lite.ingestion.correction_promotion as promotion_mod
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion.auto_promote import _build_extractors
from agent_memory_lite.mcp import stdio_handlers_episodes
from agent_memory_lite.mcp.stdio_handlers import _HANDLERS
from agent_memory_lite.mcp.stdio_tools import ALL_TOOLS
from agent_memory_lite.mcp.tools_registry_core import CORE_TOOLS
from agent_memory_lite.mcp.tools_review import memory_promote_candidate_to_behavior


def test_mcp_handler_calls_ingest_episode_with_auto_promote_settings() -> None:
    """``_handle_ingest_episode`` source must pass auto_promote_settings.

    Read the source string and assert ``auto_promote_settings=`` is
    present at the ``ingest_episode(...)`` call. A simple textual check
    is enough — if someone deletes the kwarg, the contract breaks and
    this test catches it.
    """
    src = inspect.getsource(stdio_handlers_episodes._handle_ingest_episode)
    assert "ingest_episode(" in src
    assert "auto_promote_settings=" in src


def test_settings_drives_correction_extractor_registration() -> None:
    """When ``correction_detect_enabled`` is on AND a connection is
    supplied, ``_build_extractors`` registers the CorrectionExtractor.

    This is the same code path that fires inside the MCP local-fallback
    branch (``_handle_ingest_episode`` → ``ingest_episode`` →
    ``auto_promote(conn, settings)`` → ``_build_extractors(settings, conn)``).
    """
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_CORRECTION_DETECT_ENABLED="true",
    )
    conn = sqlite3.connect(":memory:")
    try:
        names = [e.name for e in _build_extractors(settings, conn)]
        assert "correction" in names
    finally:
        conn.close()


def test_settings_off_skips_correction_extractor_for_mcp() -> None:
    """Symmetric to the parity invariant: with the master flag off,
    no CorrectionExtractor is registered for the MCP local-fallback
    path either."""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_CORRECTION_DETECT_ENABLED="false",
    )
    conn = sqlite3.connect(":memory:")
    try:
        names = [e.name for e in _build_extractors(settings, conn)]
        assert "correction" not in names
    finally:
        conn.close()


def test_tools_review_applies_to_string_defense(monkeypatch: pytest.MonkeyPatch) -> None:
    """``memory_promote_candidate_to_behavior`` (the dispatch-style
    handler in tools_review.py) must coerce a stringy ``applies_to``
    into a single-element tuple, not a per-character tuple.
    """
    captured: list = []

    def fake_promote(c, payload):
        captured.append(payload)
        raise RuntimeError("intentional short-circuit")

    # The tools_review handler does a `from ... import promote_correction_to_behavior`
    # inside its body; patch the source module so the lazy import picks it up.
    monkeypatch.setattr(promotion_mod, "promote_correction_to_behavior", fake_promote)
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(RuntimeError):
            memory_promote_candidate_to_behavior(
                conn=conn,
                candidate_id="cand_x",
                name="rule_x",
                applies_to="role,workflow",  # stringy, not a list
            )
    finally:
        conn.close()
    assert captured, "handler should have called the service"
    assert captured[0].applies_to == ("role,workflow",), (
        "stringy applies_to must produce a single-element tuple, not per-character tuple"
    )


def test_mcp_promote_to_behavior_tool_registered() -> None:
    """The new v1.10 promote tool is exposed via the MCP registry,
    the stdio tool spec list, AND the dispatch handler table."""
    name = "memory_promote_candidate_to_behavior"
    assert name in {t.name for t in CORE_TOOLS}
    assert name in {t.name for t in ALL_TOOLS}
    assert name in _HANDLERS


def test_mcp_promote_to_behavior_schema_exposes_full_field_set() -> None:
    """Lock the stdio inputSchema for memory_promote_candidate_to_behavior
    so every field the handler reads is also declared in the schema.

    Regression: in v1.2.0 audit, ``overwrite`` was being read by the
    handler at ``tools_review.memory_promote_candidate_to_behavior`` but
    was missing from ``stdio_tools_review.REVIEW_TOOLS`` — stdio clients
    couldn't pass the flag. This test prevents the gap from coming back.
    """
    name = "memory_promote_candidate_to_behavior"
    tool = next(t for t in ALL_TOOLS if t.name == name)
    properties = tool.inputSchema["properties"]
    expected = {
        "workspace_id",
        "candidate_id",
        "name",
        "rule_text_override",
        "rationale",
        "kind",
        "scope",
        "priority",
        "conflict_policy",
        "applies_to",
        "decided_by",
        "pinned",
        "overwrite",
    }
    missing = expected - properties.keys()
    assert not missing, f"stdio schema is missing fields: {sorted(missing)}"
