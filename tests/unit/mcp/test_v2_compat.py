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

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_path = tmp_path / "canonical.db"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.commit()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _reset_deprecation_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Once-per-session dedup is module-level state. Reset between tests so
    each case starts with a clean slate.

    Also ensure the suppress flag is OFF by default; individual tests can
    override via ``monkeypatch.setenv``.
    """
    monkeypatch.delenv("MEMORY_SUPPRESS_DEPRECATION_NOTICES", raising=False)
    v2_compat.reset_seen_deprecations()
    yield
    v2_compat.reset_seen_deprecations()


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


# ============================================================
# Deprecation-notice suppression — env flag + once-per-session dedup
# ============================================================


def test_deprecation_notice_dedup_within_session(conn: sqlite3.Connection) -> None:
    """First call to a v2 name emits the notice; subsequent calls drop it.

    Long-lived stdio servers see the same v2 name many times per session.
    The agent only needs the migration hint once.
    """
    args = {"workspace_id": "default", "title": "T1", "decision_text": "B"}
    env1 = v2_compat.compat_dispatch(conn, "memory_write_decision", args)
    assert env1 is not None
    assert env1["ok"] is True
    assert "deprecation_notice" in env1

    env2 = v2_compat.compat_dispatch(conn, "memory_write_decision", {**args, "title": "T2"})
    assert env2 is not None
    assert env2["ok"] is True
    assert "deprecation_notice" not in env2


def test_deprecation_notice_distinct_names_each_get_first_emit(
    conn: sqlite3.Connection,
) -> None:
    """Dedup is per v2 name — a different shim tool still emits its first notice."""
    env_a = v2_compat.compat_dispatch(
        conn,
        "memory_write_decision",
        {"workspace_id": "default", "title": "A", "decision_text": "B"},
    )
    env_b = v2_compat.compat_dispatch(
        conn,
        "memory_write_theory",
        {"workspace_id": "default", "title": "C", "claim": "D"},
    )
    assert env_a is not None
    assert "deprecation_notice" in env_a
    assert env_b is not None
    assert "deprecation_notice" in env_b


def test_deprecation_notice_env_flag_suppresses_all(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEMORY_SUPPRESS_DEPRECATION_NOTICES=true drops the field entirely."""
    monkeypatch.setenv("MEMORY_SUPPRESS_DEPRECATION_NOTICES", "true")
    env = v2_compat.compat_dispatch(
        conn,
        "memory_write_decision",
        {"workspace_id": "default", "title": "T", "decision_text": "B"},
    )
    assert env is not None
    assert env["ok"] is True
    assert "deprecation_notice" not in env


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
def test_suppress_flag_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MEMORY_SUPPRESS_DEPRECATION_NOTICES", value)
    assert v2_compat._suppress_deprecation_notices() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "random"])
def test_suppress_flag_falsy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MEMORY_SUPPRESS_DEPRECATION_NOTICES", value)
    assert v2_compat._suppress_deprecation_notices() is False


def test_deprecation_dedup_resets_between_processes() -> None:
    """The reset helper exists so tests get a clean slate. Verify it works."""
    assert v2_compat._deprecation("X", "Y") is not None  # first call emits
    assert v2_compat._deprecation("X", "Y") is None  # second call deduped (within TTL)
    v2_compat.reset_seen_deprecations()
    assert v2_compat._deprecation("X", "Y") is not None  # post-reset emits again


# ============================================================
# Audit 3 #3: TTL — long-lived processes re-emit the notice periodically
# ============================================================


def test_deprecation_ttl_default_one_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    """The TTL reader defaults to 3600 seconds (one hour)."""
    monkeypatch.delenv("MEMORY_DEPRECATION_TTL_SECONDS", raising=False)
    assert v2_compat._deprecation_ttl_seconds() == 3600


def test_deprecation_ttl_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator can tune the TTL via env."""
    monkeypatch.setenv("MEMORY_DEPRECATION_TTL_SECONDS", "120")
    assert v2_compat._deprecation_ttl_seconds() == 120


def test_deprecation_ttl_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_DEPRECATION_TTL_SECONDS", "garbage")
    assert v2_compat._deprecation_ttl_seconds() == 3600


def test_deprecation_ttl_clamps_below_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero / negative values clamp to 1s so the TTL never disables dedup."""
    monkeypatch.setenv("MEMORY_DEPRECATION_TTL_SECONDS", "0")
    assert v2_compat._deprecation_ttl_seconds() == 1


def test_deprecation_reemits_after_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the recorded last-emit timestamp is older than the TTL,
    the next call re-emits the banner (long-running server reminder)."""
    monkeypatch.setenv("MEMORY_DEPRECATION_TTL_SECONDS", "60")
    v2_compat.reset_seen_deprecations()
    assert v2_compat._deprecation("memory_X", "memory_write") is not None
    # Forcibly age the entry beyond the TTL by rewriting the timestamp.
    v2_compat._seen_deprecations["memory_X"] -= 120  # 2 minutes old
    assert v2_compat._deprecation("memory_X", "memory_write") is not None


def test_deprecation_stays_silent_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calls within the TTL window stay silent."""
    monkeypatch.setenv("MEMORY_DEPRECATION_TTL_SECONDS", "3600")
    v2_compat.reset_seen_deprecations()
    assert v2_compat._deprecation("memory_Y", "memory_write") is not None
    # Age the entry by a small amount well below TTL.
    v2_compat._seen_deprecations["memory_Y"] -= 30
    assert v2_compat._deprecation("memory_Y", "memory_write") is None
