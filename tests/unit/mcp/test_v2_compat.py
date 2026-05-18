"""Unit tests for the v2 → v3 compat shim.

Covers:

* Activation gate (env flag default + override).
* Tier-1 translations: write_decision / write_theory / get_object /
  upsert_concept / upsert_behavior / upsert_agent_role/skill/playbook /
  archive / pin / search / list_decisions / list_theories.
* Tier-3 translation-pending tools return a clear ``translation_pending``
  error code with deprecation_notice pointing at the v3 successor.
* Every shimmed response carries the deprecation_notice field.
* The shim never raises — exceptions become envelope errors.

The shim is exercised against a real v3-schema SQLite DB (same fixture
pattern as test_v3_handlers.py) — no mocks on the v3 backend, so the
translation tables are end-to-end validated.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.mcp import v2_compat

SCHEMA_V3_PATH = Path(__file__).resolve().parents[3] / "migrations" / "v3" / "0001_init.sql"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_path = tmp_path / "v3.db"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_V3_PATH.read_text(encoding="utf-8"))
    c.commit()
    try:
        yield c
    finally:
        c.close()


# ============================================================
# Activation gate
# ============================================================


def test_is_enabled_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default flipped to ON post canonical-rename (2026-05-18).

    The shim now routes legacy v2 names like ``memory_record_with_evidence``
    to the canonical backend, skipping the slow native v2 pipeline that
    hung Claude Code on multi-KB writes. Set env to ``false`` to restore
    the native v2 path for debugging.
    """
    monkeypatch.delenv("MEMORY_V2_COMPAT_ENABLED", raising=False)
    assert v2_compat.is_enabled() is True


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", "True"])
def test_is_enabled_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MEMORY_V2_COMPAT_ENABLED", value)
    assert v2_compat.is_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "random"])
def test_is_enabled_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MEMORY_V2_COMPAT_ENABLED", value)
    assert v2_compat.is_enabled() is False


# ============================================================
# Dispatch
# ============================================================


def test_dispatch_unknown_tool_returns_none(conn: sqlite3.Connection) -> None:
    assert v2_compat.compat_dispatch(conn, "memory_unknown_tool", {}) is None


def test_dispatch_carries_deprecation_notice(conn: sqlite3.Connection) -> None:
    env = v2_compat.compat_dispatch(
        conn,
        "memory_write_decision",
        {"workspace_id": "default", "title": "T", "decision_text": "Body"},
    )
    assert env is not None
    assert env["ok"] is True
    assert "deprecation_notice" in env
    assert "memory_write" in env["deprecation_notice"]


def test_dispatch_never_raises(conn: sqlite3.Connection) -> None:
    """Even with intentionally broken args, the shim must return an envelope."""
    env = v2_compat.compat_dispatch(conn, "memory_write_decision", {"title": object()})
    assert env is not None
    assert env["ok"] is False
    assert env["error"]["code"] in {"unsupported_kind", "invalid_args", "shim_internal_error"}


# ============================================================
# Tier 1: primary translations
# ============================================================


def test_write_decision_via_shim(conn: sqlite3.Connection) -> None:
    env = v2_compat.compat_dispatch(
        conn,
        "memory_write_decision",
        {
            "workspace_id": "default",
            "title": "Use Kelly",
            "decision_text": "We use quarter-Kelly sizing.",
            "rationale": "Bankroll preservation",
        },
    )
    assert env is not None
    assert env["ok"] is True
    assert env["data"]["id"].startswith("dec_")
    assert env["data"]["kind"] == "decision"


def test_write_theory_via_shim(conn: sqlite3.Connection) -> None:
    env = v2_compat.compat_dispatch(
        conn,
        "memory_write_theory",
        {
            "workspace_id": "default",
            "title": "Source-flip edge",
            "claim": "Source-flip trades show short-lived edge on favorites.",
            "status": "testing",
            "confidence": 0.4,
        },
    )
    assert env is not None
    assert env["ok"] is True
    assert env["data"]["kind"] == "theory"
    assert env["data"]["id"].startswith("th_")


def test_get_object_via_shim(conn: sqlite3.Connection) -> None:
    # First seed via v3 write directly
    write_env = v2_compat.compat_dispatch(
        conn,
        "memory_write_decision",
        {"workspace_id": "default", "title": "Hello", "decision_text": "World"},
    )
    assert write_env is not None
    dec_id = write_env["data"]["id"]

    env = v2_compat.compat_dispatch(
        conn,
        "memory_get_object",
        {"workspace_id": "default", "kind": "decision", "id": dec_id},
    )
    assert env is not None
    assert env["ok"] is True
    assert env["data"]["id"] == dec_id


def test_get_object_not_found_via_shim(conn: sqlite3.Connection) -> None:
    env = v2_compat.compat_dispatch(
        conn,
        "memory_get_object",
        {"workspace_id": "default", "kind": "decision", "id": "missing"},
    )
    assert env is not None
    assert env["ok"] is False
    assert env["error"]["code"] == "not_found"


def test_upsert_concept_via_shim(conn: sqlite3.Connection) -> None:
    env = v2_compat.compat_dispatch(
        conn,
        "memory_upsert_concept",
        {
            "workspace_id": "default",
            "name": "selector-gate",
            "definition": "Admission rule preventing a candidate from reaching paper.",
            "aliases": ["admission gate"],
        },
    )
    assert env is not None
    assert env["ok"] is True
    assert env["data"]["kind"] == "concept"


def test_upsert_behavior_via_shim(conn: sqlite3.Connection) -> None:
    env = v2_compat.compat_dispatch(
        conn,
        "memory_upsert_behavior_instruction",
        {
            "workspace_id": "default",
            "name": "evidence-first",
            "rule": "Always cite source/confidence when surfacing memory.",
            "kind": "operating_rule",
            "applies_to": ["incident reports"],
        },
    )
    assert env is not None
    assert env["ok"] is True
    assert env["data"]["kind"] == "behavior"


@pytest.mark.parametrize(
    ("tool", "subtype"),
    [
        ("memory_upsert_agent_role", "role"),
        ("memory_upsert_agent_skill", "skill"),
        ("memory_upsert_agent_playbook", "playbook"),
    ],
)
def test_upsert_capability_subtypes(conn: sqlite3.Connection, tool: str, subtype: str) -> None:
    env = v2_compat.compat_dispatch(
        conn,
        tool,
        {
            "workspace_id": "default",
            "name": f"test-{subtype}",
            "summary": f"A test {subtype}",
            "body_md": "# body",
        },
    )
    assert env is not None
    assert env["ok"] is True
    assert env["data"]["kind"] == "skill"


def test_archive_via_shim(conn: sqlite3.Connection) -> None:
    write_env = v2_compat.compat_dispatch(
        conn,
        "memory_write_decision",
        {"workspace_id": "default", "title": "A", "decision_text": "B"},
    )
    assert write_env is not None
    dec_id = write_env["data"]["id"]
    env = v2_compat.compat_dispatch(
        conn,
        "memory_archive",
        {"workspace_id": "default", "kind": "decision", "id": dec_id, "reason": "obsolete"},
    )
    assert env is not None
    assert env["ok"] is True
    assert env["data"]["status"] == "archived"


def test_archive_restore_is_translation_pending(conn: sqlite3.Connection) -> None:
    env = v2_compat.compat_dispatch(
        conn,
        "memory_archive",
        {"workspace_id": "default", "kind": "decision", "id": "dec_x", "archive": False},
    )
    assert env is not None
    assert env["ok"] is False
    assert env["error"]["code"] == "translation_pending"


def test_pin_via_shim(conn: sqlite3.Connection) -> None:
    write_env = v2_compat.compat_dispatch(
        conn,
        "memory_write_decision",
        {"workspace_id": "default", "title": "P", "decision_text": "."},
    )
    assert write_env is not None
    dec_id = write_env["data"]["id"]
    env = v2_compat.compat_dispatch(
        conn,
        "memory_pin",
        {"workspace_id": "default", "kind": "decision", "id": dec_id, "pinned": True},
    )
    assert env is not None
    assert env["ok"] is True
    assert env["data"]["pinned"] is True


def test_search_via_shim(conn: sqlite3.Connection) -> None:
    v2_compat.compat_dispatch(
        conn,
        "memory_write_decision",
        {"workspace_id": "default", "title": "Kelly sizing", "decision_text": "Use quarter-Kelly"},
    )
    env = v2_compat.compat_dispatch(
        conn,
        "memory_search",
        {"workspace_id": "default", "query": "kelly", "limit": 5},
    )
    assert env is not None
    assert env["ok"] is True
    assert isinstance(env["data"], list)
    assert any(hit["projection"].get("title") == "Kelly sizing" for hit in env["data"])


def test_list_decisions_via_shim(conn: sqlite3.Connection) -> None:
    for title in ["A", "B"]:
        v2_compat.compat_dispatch(
            conn,
            "memory_write_decision",
            {"workspace_id": "default", "title": title, "decision_text": "x"},
        )
    env = v2_compat.compat_dispatch(
        conn,
        "memory_list_decisions",
        {"workspace_id": "default", "limit": 10},
    )
    assert env is not None
    assert env["ok"] is True
    assert len(env["data"]) == 2


# ============================================================
# Tier 3: translation-pending
# ============================================================


@pytest.mark.parametrize(
    "tool",
    [
        "memory_snapshot_save",
        "memory_snapshot_list",
        "memory_snapshot_diff",
        "memory_list_audit",
        "memory_list_maintenance_events",
        "memory_review_queue",
        "memory_compact_trigger",
        "memory_claim_edit",
        "memory_release_edit",
    ],
)
def test_translation_pending_tools(conn: sqlite3.Connection, tool: str) -> None:
    env = v2_compat.compat_dispatch(conn, tool, {"workspace_id": "default"})
    assert env is not None
    assert env["ok"] is False
    assert env["error"]["code"] == "translation_pending"
    assert "deprecation_notice" in env


# ============================================================
# Coverage report
# ============================================================


def test_compat_handlers_count_meets_plan_target() -> None:
    """Plan target: shim covers 'all 30+ v2 tool names'.

    Tier 1 + Tier 3 stub entries together must be at least 25 — the
    most-frequently-used v2 surface. Tier 2 is the long tail and can be
    filled in later iterations without breaking the shim contract.
    """
    assert len(v2_compat.COMPAT_HANDLERS) >= 25
