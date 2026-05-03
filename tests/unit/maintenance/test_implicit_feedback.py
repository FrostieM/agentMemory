"""Implicit feedback derives feedback rows from existing operator actions.

The flag must default-off so workspaces that haven't opted in see no
behaviour change. When on, archive/promote/link helpers must each
produce one ``memory_usage_feedback`` row with the expected
``source='implicit_*'`` value.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.implicit_feedback import (
    record_implicit_archive,
    record_implicit_link,
    record_implicit_promote,
)


def _settings(*, enabled: bool) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_IMPLICIT_FEEDBACK_ENABLED="true" if enabled else "false",
    )


def _feedback_row(conn: sqlite3.Connection) -> tuple[str, float, str] | None:
    row = conn.execute(
        "SELECT source_type, usefulness, source FROM memory_usage_feedback LIMIT 1"
    ).fetchone()
    return None if row is None else (str(row[0]), float(row[1]), str(row[2]))


def _feedback_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM memory_usage_feedback").fetchone()[0])


def test_archive_writes_negative_feedback(applied_conn: sqlite3.Connection) -> None:
    settings = _settings(enabled=True)
    written = record_implicit_archive(
        applied_conn,
        settings=settings,
        workspace_id="default",
        source_type="chunk",
        source_id="chk_x",
    )
    assert written is True
    row = _feedback_row(applied_conn)
    assert row is not None
    assert row == ("chunk", -1.0, "implicit_archive")


def test_promote_writes_positive_feedback(applied_conn: sqlite3.Connection) -> None:
    settings = _settings(enabled=True)
    written = record_implicit_promote(
        applied_conn,
        settings=settings,
        workspace_id="default",
        source_type="decision",
        source_id="dec_x",
    )
    assert written is True
    row = _feedback_row(applied_conn)
    assert row is not None
    assert row == ("decision", 0.7, "implicit_promote")


def test_link_uses_strength_as_weight(applied_conn: sqlite3.Connection) -> None:
    settings = _settings(enabled=True)
    written = record_implicit_link(
        applied_conn,
        settings=settings,
        workspace_id="default",
        target_type="theory",
        target_id="th_x",
        strength=0.8,
    )
    assert written is True
    row = _feedback_row(applied_conn)
    assert row is not None
    assert row[0] == "theory"
    assert row[1] == 0.8
    assert row[2] == "implicit_link"


def test_flag_off_writes_nothing(applied_conn: sqlite3.Connection) -> None:
    settings = _settings(enabled=False)
    assert (
        record_implicit_archive(
            applied_conn,
            settings=settings,
            workspace_id="default",
            source_type="chunk",
            source_id="chk_x",
        )
        is False
    )
    assert _feedback_count(applied_conn) == 0


def test_unsupported_source_type_skipped(applied_conn: sqlite3.Connection) -> None:
    settings = _settings(enabled=True)
    assert (
        record_implicit_archive(
            applied_conn,
            settings=settings,
            workspace_id="default",
            source_type="bogus",
            source_id="x",
        )
        is False
    )
    assert _feedback_count(applied_conn) == 0


def test_link_with_unsupported_target_type_skipped(applied_conn: sqlite3.Connection) -> None:
    settings = _settings(enabled=True)
    # capability_links can target experiments / theory_evidence / candidates,
    # but those don't map to memory_usage_feedback source_type — skip silently.
    assert (
        record_implicit_link(
            applied_conn,
            settings=settings,
            workspace_id="default",
            target_type="experiment",
            target_id="exp_x",
            strength=0.9,
        )
        is False
    )
    assert _feedback_count(applied_conn) == 0


def test_link_with_zero_strength_skipped(applied_conn: sqlite3.Connection) -> None:
    settings = _settings(enabled=True)
    assert (
        record_implicit_link(
            applied_conn,
            settings=settings,
            workspace_id="default",
            target_type="theory",
            target_id="th_x",
            strength=0.0,
        )
        is False
    )
    assert _feedback_count(applied_conn) == 0


def test_link_strength_clamped_to_unit_interval(applied_conn: sqlite3.Connection) -> None:
    settings = _settings(enabled=True)
    record_implicit_link(
        applied_conn,
        settings=settings,
        workspace_id="default",
        target_type="theory",
        target_id="th_x",
        strength=99.5,  # absurd value
    )
    row = _feedback_row(applied_conn)
    assert row is not None
    assert row[1] == 1.0  # clamped
