"""Unit tests for v3 MCP handlers.

Each handler is called directly with a stubbed ``_runtime.db()`` that
points at an isolated v3-schema SQLite database. Envelope shape
matches what the HTTP routes return — verified by inspecting the
top-level ``{ok, data, error}`` keys.

The MCP dispatcher is exercised in the broader stdio_server test;
here we focus on the handler bodies.
"""

from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.config.workspace_registry import WorkspaceRegistry
from agent_memory_lite.mcp import stdio_handlers_memory as v3
from agent_memory_lite.mcp.stdio_handlers import _HANDLERS
from agent_memory_lite.mcp.stdio_tools import ALL_TOOLS
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS


@pytest.fixture
def db_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """Fresh v3-schema DB. Swaps `_runtime.db()` to return this conn.

    Also swaps `_runtime.settings` for a copy with hub_mode=True and
    strict_workspace_isolation=False so the v3 write guard does not
    block writes to the test workspace ("default").
    """
    db_path = tmp_path / "canonical.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(conn)
    conn.commit()
    # Patch the runtime db accessor used by every v3 handler.
    monkeypatch.setattr(v3._runtime, "db", lambda: conn)
    # Settings is a frozen pydantic model — replace the whole instance.
    # db_path MUST point at this temp DB: the v3 write handlers run the
    # physical-file backstop (ensure_workspace_matches_db) unconditionally, so
    # the anchor's authoritative path (settings.db_path) has to match the conn
    # SQLite actually opened, else every anchor write would be falsely rejected.
    relaxed = v3._runtime.settings.model_copy(
        update={
            "workspace_id": "default",
            "db_path": db_path,
            "strict_workspace_isolation": False,
            "forbid_default_workspace": False,
            "hub_mode": True,
        }
    )
    monkeypatch.setattr(v3._runtime, "settings", relaxed)
    try:
        yield conn
    finally:
        conn.close()


def _seed_decision(conn: sqlite3.Connection, **kwargs: object) -> str:
    payload = {"title": "T", "decision_text": "Body of the decision."}
    payload.update(kwargs)
    env = v3._handle_v3_write({"workspace_id": "default", "kind": "decision", "payload": payload})
    assert env["ok"] is True, env
    assert isinstance(env["data"], dict)
    return str(env["data"]["id"])


# ============================================================
# Registration: every v3 tool has a matching handler
# ============================================================


def test_v3_tools_match_handlers() -> None:
    # Canonical v3 surface: names are version-free post 2026-05-18 rename.
    canonical = {
        "memory_search",
        "memory_get",
        "memory_write",
        "memory_edit",
        "memory_pin",
        "memory_archive",
        "memory_brief",
        "memory_lint",
        "memory_invoke_skill",
        "memory_impact_check",
        "memory_status",
        "memory_plan",
    }
    tool_names = {t.name for t in ALL_TOOLS} & canonical
    handler_names = set(_HANDLERS) & canonical
    assert tool_names == canonical, f"missing tools: {canonical - tool_names}"
    assert handler_names == canonical, f"missing handlers: {canonical - handler_names}"
    # 6 strict + 2 hook + invoke_skill + impact_check + status + plan
    assert len(canonical) == 12


# ============================================================
# Envelope shape — uniform across handlers
# ============================================================


def test_envelope_shape_on_success(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn)
    env = v3._handle_v3_get({"workspace_id": "default", "kind": "decision", "id": dec_id})
    assert set(env.keys()) == {"ok", "data", "error"}
    assert env["ok"] is True
    assert env["error"] is None
    assert isinstance(env["data"], dict)


def test_envelope_shape_on_error(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_get({"workspace_id": "default", "kind": "decision", "id": "missing"})
    assert env["ok"] is False
    assert env["data"] is None
    assert env["error"]["code"] == "not_found"


def test_get_unknown_kind_returns_controlled_error(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_get({"workspace_id": "default", "kind": "not_a_kind", "id": "x"})
    assert env["ok"] is False
    assert env["data"] is None
    assert env["error"]["code"] == "validation_failed"


@pytest.mark.parametrize(
    ("handler_name", "args"),
    [
        (
            "_handle_v3_write",
            {
                "workspace_id": "rogue_unregistered",
                "kind": "decision",
                "payload": {"title": "Rogue", "decision_text": "must not persist"},
            },
        ),
        (
            "_handle_v3_write",
            {
                "workspace_id": "rogue_unregistered",
                "kind": "episode",
                "payload": {
                    "source_type": "agent_action",
                    "raw_text": "valid rogue episode must reject before vector deps",
                },
            },
        ),
        (
            "_handle_v3_edit",
            {
                "workspace_id": "rogue_unregistered",
                "kind": "decision",
                "id": "dec_rogue",
                "fields": {"status": "superseded"},
            },
        ),
        (
            "_handle_v3_pin",
            {
                "workspace_id": "rogue_unregistered",
                "kind": "decision",
                "id": "dec_rogue",
                "pinned": True,
            },
        ),
        (
            "_handle_v3_archive",
            {
                "workspace_id": "rogue_unregistered",
                "kind": "decision",
                "id": "dec_rogue",
                "reason": "must not persist",
            },
        ),
    ],
)
def test_mutation_handlers_reject_unregistered_workspace_before_runtime_deps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler_name: str,
    args: dict[str, object],
) -> None:
    relaxed = v3._runtime.settings.model_copy(
        update={
            "workspace_id": "anchor",
            "workspaces_file": tmp_path / "workspaces.json",
            "strict_workspace_isolation": False,
            "forbid_default_workspace": False,
            "hub_mode": True,
        }
    )
    monkeypatch.setattr(v3._runtime, "settings", relaxed)

    def fail_db_for(_workspace_id: str | None) -> sqlite3.Connection:
        raise AssertionError("unregistered mutation must reject before db_for fallback")

    def fail_dep(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("unregistered mutation must reject before runtime dependencies")

    monkeypatch.setattr(v3._runtime, "db_for", fail_db_for)
    monkeypatch.setattr(v3._runtime, "provider", fail_dep)
    monkeypatch.setattr(v3._runtime, "store_for", fail_dep)

    handler = getattr(v3, handler_name)
    with pytest.raises(ValueError, match="is not registered"):
        handler(args)


# ============================================================
# Read handlers
# ============================================================


def test_search_returns_projections(db_conn: sqlite3.Connection) -> None:
    _seed_decision(db_conn, title="Kelly sizing", decision_text="Use quarter-Kelly")
    _seed_decision(db_conn, title="Unrelated", decision_text="Other")
    env = v3._handle_v3_search({"workspace_id": "default", "query": "kelly", "limit": 5})
    assert env["ok"] is True
    assert isinstance(env["data"], list)
    titles = [hit["projection"]["title"] for hit in env["data"]]
    assert "Kelly sizing" in titles


def test_search_requires_query(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_search({"workspace_id": "default", "query": ""})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_search_unknown_kind_returns_controlled_error(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_search(
        {"workspace_id": "default", "query": "kelly", "kinds": ["decisions"]}
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"
    assert "unknown kinds" in env["error"]["message"]


def test_get_with_fields_csv(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn, decision_text="The full body text here.")
    env = v3._handle_v3_get(
        {
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": "decision_text,rationale",
        }
    )
    assert env["data"]["decision_text"] == "The full body text here."


def test_get_with_fields_list(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn, decision_text="Body B")
    env = v3._handle_v3_get(
        {
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": ["decision_text"],
        }
    )
    assert env["data"]["decision_text"] == "Body B"


# ============================================================
# Write handlers
# ============================================================


def test_write_returns_compact_projection(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_write(
        {
            "workspace_id": "default",
            "kind": "decision",
            "payload": {"title": "Hello", "decision_text": "Body"},
            "agent_id": "test-agent",
        }
    )
    assert env["ok"] is True
    assert env["data"]["kind"] == "decision"
    assert env["data"]["id"].startswith("dec_")
    assert "decision_text" not in env["data"]  # compact projection


def test_write_non_episode_does_not_resolve_runtime_vector_deps(
    db_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_dep(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("non-episode writes must not resolve vector dependencies")

    monkeypatch.setattr(v3._runtime, "provider", fail_dep)
    monkeypatch.setattr(v3._runtime, "store_for", fail_dep)

    env = v3._handle_v3_write(
        {
            "workspace_id": "default",
            "kind": "decision",
            "payload": {
                "title": "MCP scalar write",
                "decision_text": "Decision writes should not require embedding dependencies.",
            },
        }
    )

    assert env["ok"] is True, env
    assert env["data"]["kind"] == "decision"
    assert env["data"]["title"] == "MCP scalar write"


def test_write_invalid_episode_rejects_before_runtime_vector_deps(
    db_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_dep(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("invalid episode payload must reject before vector dependencies")

    monkeypatch.setattr(v3._runtime, "provider", fail_dep)
    monkeypatch.setattr(v3._runtime, "store_for", fail_dep)

    env = v3._handle_v3_write(
        {
            "workspace_id": "default",
            "kind": "episode",
            "payload": {"raw_text": "missing required source_type"},
        }
    )

    assert env["ok"] is False, env
    assert env["error"]["code"] == "invalid_args"


def test_write_episode_embeds_via_runtime_vector_deps(
    db_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    monkeypatch.setattr(v3._runtime, "provider", lambda: fake_embedding_provider)
    monkeypatch.setattr(v3._runtime, "store_for", lambda workspace_id: fake_vector_store)

    env = v3._handle_v3_write(
        {
            "workspace_id": "default",
            "kind": "episode",
            "payload": {
                "source_type": "agent_action",
                "raw_text": "canonical MCP episode should be embedded",
            },
        }
    )

    assert env["ok"] is True, env
    assert env["data"]["embedded"] is True
    chunk_id = env["data"]["chunk_id"]
    assert fake_vector_store.count(NAMESPACE_CHUNKS, workspace_id="default") == 1
    chunk = db_conn.execute("SELECT embedding_id FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    assert chunk["embedding_id"] == chunk_id


def test_write_unsupported_kind(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_write(
        {
            "workspace_id": "default",
            "kind": "nonexistent",
            "payload": {"title": "x"},
        }
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "unsupported_kind"


def test_write_invalid_args(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_write({"workspace_id": "default", "kind": "decision"})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_edit_partial_update(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn, title="v1")
    env = v3._handle_v3_edit(
        {
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": {"status": "superseded"},
        }
    )
    assert env["ok"] is True
    assert env["data"]["status"] == "superseded"


def test_edit_requires_fields_object(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_edit(
        {"workspace_id": "default", "kind": "decision", "id": "dec_x", "fields": "not-a-dict"}
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_pin_toggle(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn, title="P")
    env = v3._handle_v3_pin(
        {"workspace_id": "default", "kind": "decision", "id": dec_id, "pinned": True}
    )
    assert env["ok"] is True
    assert env["data"]["pinned"] is True
    env_off = v3._handle_v3_pin(
        {"workspace_id": "default", "kind": "decision", "id": dec_id, "pinned": False}
    )
    assert env_off["data"]["pinned"] is False


def test_archive_decision(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn, title="A")
    env = v3._handle_v3_archive(
        {
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "reason": "obsolete",
        }
    )
    assert env["ok"] is True
    assert env["data"]["status"] == "archived"


# ============================================================
# Hook primitives + invoke_skill
# ============================================================


def test_brief_composes_session_start(db_conn: sqlite3.Connection) -> None:
    _seed_decision(db_conn, title="One")
    env = v3._handle_v3_brief({"workspace_id": "default", "max_tokens": 500})
    assert env["ok"] is True
    assert env["data"]["token_count"] <= 500
    assert "identity" in env["data"]["sections"]


def test_lint_empty_workspace_allows(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_lint(
        {
            "workspace_id": "default",
            "tool_name": "Edit",
            "tool_payload": {"file_path": "x.py"},
        }
    )
    assert env["ok"] is True
    assert env["data"]["verdict"] == "allow"


def test_lint_invalid_tool_name(db_conn: sqlite3.Connection) -> None:
    # tool_payload defaults to {} when omitted; tool_name="" is the invalid case.
    env = v3._handle_v3_lint({"workspace_id": "default", "tool_name": "", "tool_payload": {}})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_invoke_skill_not_found(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_invoke_skill({"workspace_id": "default", "skill_id": "missing"})
    assert env["ok"] is False
    assert env["error"]["code"] == "not_found"


def test_invoke_skill_requires_id(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_invoke_skill({"workspace_id": "default"})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


# ============================================================
# memory_impact_check (discipline primitive)
# ============================================================


def test_impact_check_not_indexed_envelope(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_impact_check({"workspace_id": "default", "file_path": "src/missing.py"})
    assert env["ok"] is True
    assert env["data"]["verdict"] == "not_indexed"
    assert "not in code_digests" in env["data"]["advisory"]


def test_impact_check_requires_file_path(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_impact_check({"workspace_id": "default"})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_impact_check_returns_full_envelope_shape(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_impact_check({"workspace_id": "default", "file_path": "src/foo.py"})
    assert env["ok"] is True
    data = env["data"]
    assert set(data.keys()) == {
        "file_path",
        "digest",
        "callers",
        "hot_symbols",
        "verdict",
        "advisory",
    }


# ============================================================
# memory_status (diagnostic / self-introspection)
# ============================================================


def test_status_includes_environment_by_default(db_conn: sqlite3.Connection) -> None:
    """``memory_status`` defaults to ``include_environment=True`` — that is
    the whole point of the tool (closes the 'agent guesses env' footgun)."""
    env = v3._handle_v3_status({"workspace_id": "default"})
    assert env["ok"] is True
    data = env["data"]
    assert "memory" in data
    assert "code_memory" in data
    assert "adoption" in data
    assert "environment" in data
    env_block = data["environment"]
    assert env_block["anchor_workspace_id"]
    assert env_block["anchor_db_path"]
    assert isinstance(env_block["hub_mode"], bool)
    assert isinstance(env_block["registry_workspaces"], list)
    # applied_migrations is a list. In this fixture the schema is
    # ``executescript``-applied (not via apply_migrations) so the
    # schema_migrations table stays empty — we just assert the shape.
    assert isinstance(env_block["applied_migrations"], list)


def test_status_environment_can_be_disabled(db_conn: sqlite3.Connection) -> None:
    """``include_environment=False`` returns counts/adoption only."""
    env = v3._handle_v3_status({"workspace_id": "default", "include_environment": False})
    assert env["ok"] is True
    assert env["data"].get("environment") is None


def test_status_counts_reflect_writes(db_conn: sqlite3.Connection) -> None:
    """After seeding a decision the memory.decisions_active count is non-zero."""
    _seed_decision(db_conn, title="status-smoke")
    env = v3._handle_v3_status({"workspace_id": "default"})
    assert env["ok"] is True
    assert env["data"]["memory"]["decisions_active"] >= 1


# ============================================================
# Registry routing — handlers must use db_for(workspace_id), not db()
# ============================================================
#
# Regression for the 2026-05-19 search-empty bug: when the MCP server's
# anchor is misconfigured (e.g. bound to copyBot while the operator asks
# for agent-memory-lite), `_runtime.db()` returns the anchor DB and every
# read is filtered against rows that live in another DB — search returns
# empty even though the data exists. The fix routes through
# `_runtime.db_for(workspace_id)` so an explicit, registered workspace
# resolves to the right DB. The anchor remains the fallback.


def test_search_routes_via_db_for_not_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Search for workspace_id='foreign' must hit the foreign DB.

    If the handler still calls ``_runtime.db()``, the search filter against
    the anchor DB (which has no foreign-workspace rows) returns []. The
    fix makes the handler call ``_runtime.db_for(workspace_id)`` which
    consults the registry first.
    """
    anchor_path = tmp_path / "anchor.db"
    foreign_path = tmp_path / "foreign.db"
    workspaces_file = tmp_path / "workspaces.json"
    WorkspaceRegistry(workspaces_file).register(
        workspace_id="foreign",
        db_path=str(foreign_path),
        vector_path=str(tmp_path / "foreign.lance"),
    )
    anchor = sqlite3.connect(anchor_path)
    foreign = sqlite3.connect(foreign_path)
    for conn in (anchor, foreign):
        conn.row_factory = sqlite3.Row
        from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

        apply_migrations(conn)
        conn.commit()

    relaxed = v3._runtime.settings.model_copy(
        update={
            "workspace_id": "anchor",
            "workspaces_file": workspaces_file,
            "strict_workspace_isolation": False,
            "forbid_default_workspace": False,
            "hub_mode": True,
        }
    )
    monkeypatch.setattr(v3._runtime, "settings", relaxed)
    monkeypatch.setattr(v3._runtime, "db", lambda: anchor)
    seen: dict[str, str] = {}

    def fake_db_for(workspace_id: str | None) -> sqlite3.Connection:
        seen["workspace_id"] = workspace_id or ""
        if workspace_id == "foreign":
            return foreign
        return anchor

    monkeypatch.setattr(v3._runtime, "db_for", fake_db_for)

    # Seed ONLY in foreign DB. If routing is broken, search hits anchor → empty.
    seed_env = v3._handle_v3_write(
        {
            "workspace_id": "foreign",
            "kind": "decision",
            "payload": {"title": "Routed Kelly sizing", "decision_text": "Body"},
        }
    )
    assert seed_env["ok"], seed_env
    assert seen["workspace_id"] == "foreign"

    env = v3._handle_v3_search({"workspace_id": "foreign", "query": "kelly", "limit": 5})
    assert env["ok"] is True
    titles = [hit["projection"]["title"] for hit in env["data"]]
    assert "Routed Kelly sizing" in titles, env

    # Sanity: search with workspace_id='anchor' against the anchor DB finds nothing.
    env_anchor = v3._handle_v3_search({"workspace_id": "anchor", "query": "kelly", "limit": 5})
    assert env_anchor["ok"] is True
    assert env_anchor["data"] == []
    anchor.close()
    foreign.close()


def test_get_routes_via_db_for_not_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """memory_get(workspace_id='foreign', id=X) must hit the foreign DB."""
    anchor_path = tmp_path / "anchor.db"
    foreign_path = tmp_path / "foreign.db"
    workspaces_file = tmp_path / "workspaces.json"
    WorkspaceRegistry(workspaces_file).register(
        workspace_id="foreign",
        db_path=str(foreign_path),
        vector_path=str(tmp_path / "foreign.lance"),
    )
    anchor = sqlite3.connect(anchor_path)
    foreign = sqlite3.connect(foreign_path)
    for conn in (anchor, foreign):
        conn.row_factory = sqlite3.Row
        from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

        apply_migrations(conn)
        conn.commit()

    relaxed = v3._runtime.settings.model_copy(
        update={
            "workspace_id": "anchor",
            "workspaces_file": workspaces_file,
            "strict_workspace_isolation": False,
            "forbid_default_workspace": False,
            "hub_mode": True,
        }
    )
    monkeypatch.setattr(v3._runtime, "settings", relaxed)
    monkeypatch.setattr(v3._runtime, "db", lambda: anchor)
    monkeypatch.setattr(
        v3._runtime,
        "db_for",
        lambda wsid: foreign if wsid == "foreign" else anchor,
    )

    seed_env = v3._handle_v3_write(
        {
            "workspace_id": "foreign",
            "kind": "decision",
            "payload": {"title": "Foreign-only", "decision_text": "Body"},
        }
    )
    assert seed_env["ok"], seed_env
    dec_id = str(seed_env["data"]["id"])

    env = v3._handle_v3_get({"workspace_id": "foreign", "kind": "decision", "id": dec_id})
    assert env["ok"] is True
    assert env["data"]["id"] == dec_id

    env_anchor = v3._handle_v3_get({"workspace_id": "anchor", "kind": "decision", "id": dec_id})
    assert env_anchor["ok"] is False
    assert env_anchor["error"]["code"] == "not_found"
    anchor.close()
    foreign.close()


# ============================================================
# plan_step write routing (Debt C — rank auto-assignment)
# ============================================================


def test_write_plan_step_auto_ranks(db_conn: sqlite3.Connection) -> None:
    """memory_write(kind=plan_step) succeeds with no explicit rank: the
    handler routes plan_step through add_plan_step_from_payload, which
    assigns rank — the generic writer would fail the NOT NULL constraint."""
    env = v3._handle_v3_write(
        {
            "workspace_id": "default",
            "kind": "plan_step",
            "payload": {"task_id": "t1", "title": "step one"},
        }
    )
    assert env["ok"] is True, env
    assert env["data"]["kind"] == "plan_step"
    assert env["data"]["rank"] == 1.0


def test_write_plan_step_invalid_payload(db_conn: sqlite3.Connection) -> None:
    """A plan_step payload missing a required field maps to invalid_args."""
    env = v3._handle_v3_write(
        {"workspace_id": "default", "kind": "plan_step", "payload": {"task_id": "t1"}}
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


# ============================================================
# memory_plan (read a plan's steps by task_id)
# ============================================================


def _seed_plan_step(conn: sqlite3.Connection, task_id: str, title: str) -> str:
    env = v3._handle_v3_write(
        {
            "workspace_id": "default",
            "kind": "plan_step",
            "payload": {"task_id": task_id, "title": title},
        }
    )
    assert env["ok"] is True, env
    assert isinstance(env["data"], dict)
    return str(env["data"]["id"])


def test_plan_returns_steps_rank_ordered(db_conn: sqlite3.Connection) -> None:
    """memory_plan lists a task's live steps as projections in rank order."""
    for title in ("alpha", "beta", "gamma"):
        _seed_plan_step(db_conn, "t1", title)
    env = v3._handle_v3_plan({"workspace_id": "default", "task_id": "t1"})
    assert env["ok"] is True
    assert [s["title"] for s in env["data"]] == ["alpha", "beta", "gamma"]
    assert all(s["kind"] == "plan_step" for s in env["data"])


def test_plan_empty_for_unknown_task(db_conn: sqlite3.Connection) -> None:
    """An unknown task_id yields an empty list, not an error."""
    env = v3._handle_v3_plan({"workspace_id": "default", "task_id": "no-such-task"})
    assert env["ok"] is True
    assert env["data"] == []


def test_plan_requires_task_id(db_conn: sqlite3.Connection) -> None:
    """memory_plan without a task_id is an invalid_args error."""
    env = v3._handle_v3_plan({"workspace_id": "default"})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_plan_excludes_removed_steps(db_conn: sqlite3.Connection) -> None:
    """A re-planned-out step (valid_to set) drops out of memory_plan."""
    _seed_plan_step(db_conn, "t1", "kept")
    removed_id = _seed_plan_step(db_conn, "t1", "removed")
    v3._handle_v3_edit(
        {
            "workspace_id": "default",
            "kind": "plan_step",
            "id": removed_id,
            "fields": {"valid_to": "2099-01-01T00:00:00Z"},
        }
    )
    env = v3._handle_v3_plan({"workspace_id": "default", "task_id": "t1"})
    assert env["ok"] is True
    assert [s["title"] for s in env["data"]] == ["kept"]


def _raw_plan_step(
    conn: sqlite3.Connection,
    *,
    step_id: str,
    task_id: str,
    rank: float,
    title: str,
    created_at: str,
) -> None:
    """Insert a plan_steps row directly, bypassing the auto-rank writer.

    Lets a test pin ``rank`` and ``created_at`` exactly -- the only way
    to construct a deterministic rank tie.
    """
    conn.execute(
        "INSERT INTO plan_steps "
        "(id, workspace_id, task_id, rank, title, valid_from, created_at, updated_at) "
        "VALUES (?, 'default', ?, ?, ?, ?, ?, ?)",
        (step_id, task_id, rank, title, created_at, created_at, created_at),
    )
    conn.commit()


def test_plan_rank_ties_break_by_created_at(db_conn: sqlite3.Connection) -> None:
    """Equal-rank steps order deterministically by created_at, then id.

    The two steps share rank 5.0 and are inserted later-then-earlier,
    so a plain ``ORDER BY rank`` would return them in insertion order
    and fail this assertion -- the tiebreaker is what makes it pass.
    """
    _raw_plan_step(
        db_conn,
        step_id="step_b",
        task_id="t1",
        rank=5.0,
        title="later",
        created_at="2026-01-02T00:00:00+00:00",
    )
    _raw_plan_step(
        db_conn,
        step_id="step_a",
        task_id="t1",
        rank=5.0,
        title="earlier",
        created_at="2026-01-01T00:00:00+00:00",
    )
    env = v3._handle_v3_plan({"workspace_id": "default", "task_id": "t1"})
    assert env["ok"] is True
    assert [s["title"] for s in env["data"]] == ["earlier", "later"]


# ============================================================
# Physical-file backstop (audit C1/F1/F2): every mutation handler rejects a
# misrouted connection with a canonical, filesystem-path-free envelope.
# ============================================================


def _misroute_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point settings.db_path at a file the routed conn is NOT.

    db_for still returns the db_conn fixture's temp conn, so the connection's
    physical file no longer matches the anchor's authoritative db_path -- the
    same shape as a stale registry / wrong X-Memory-DB-Path / pre-middleware
    service that the 2026-05-21 leak exploited.
    """
    misrouted = v3._runtime.settings.model_copy(
        update={"db_path": Path("Z:/nonexistent/wrong-workspace.db")}
    )
    monkeypatch.setattr(v3._runtime, "settings", misrouted)


def test_write_backstop_rejects_misrouted_conn(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An anchor write whose routed conn is not the anchor's authoritative DB
    is rejected BEFORE any row is written.

    Pins F1 (the old ``ws != anchor`` gate skipped the backstop for the anchor,
    leaving exactly this misroute unguarded) and F2 (canonical envelope, no
    absolute filesystem path leaked to the client).
    """
    _misroute_settings(monkeypatch)
    env = v3._handle_v3_write(
        {
            "workspace_id": "default",
            "kind": "decision",
            "payload": {"title": "T", "decision_text": "should never be written"},
        }
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "validation_failed"
    msg = env["error"]["message"]
    assert "Z:" not in msg
    assert "wrong-workspace.db" not in msg
    # Nothing was persisted -- the backstop runs before write_canonical.
    assert db_conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_edit_backstop_rejects_misrouted_conn(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """edit/pin/archive share the write backstop: a misrouted conn yields the
    same canonical rejection (exercises the non-write handlers' try/except)."""
    dec_id = _seed_decision(db_conn)  # seeded while db_path still matches the conn
    _misroute_settings(monkeypatch)
    env = v3._handle_v3_edit(
        {
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": {"title": "tampered"},
        }
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "validation_failed"
    assert "Z:" not in env["error"]["message"]


def test_all_mutation_handlers_carry_db_backstop() -> None:
    """Static invariant -- the MCP analogue of the HTTP guard-coverage test.

    Every v3 mutation handler must run ``ensure_workspace_matches_db``; this
    fails loudly if a future edit silently drops the backstop from one handler.
    """
    for fn in (
        v3._handle_v3_write,
        v3._handle_v3_edit,
        v3._handle_v3_pin,
        v3._handle_v3_archive,
    ):
        src = inspect.getsource(fn)
        assert "ensure_workspace_matches_db" in src, f"{fn.__name__} lost the backstop"
    # The write handler additionally carries the vector-store backstop, since the
    # episode branch routes a LanceDB store on a path separate from the SQL conn.
    assert "ensure_store_matches_workspace" in inspect.getsource(v3._handle_v3_write)
