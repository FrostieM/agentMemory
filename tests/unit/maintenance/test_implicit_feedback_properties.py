"""Property-based tests for implicit feedback (hypothesis).

Pins numeric invariants of the v2.1 implicit-feedback module that
example-based tests would not surface:

* Link strength is clamped to ``[0, 1]`` for any real input, including
  negative, NaN-adjacent, and absurd values.
* Archive helper always writes ``-1.0`` regardless of operator-supplied
  source_type when the type is supported.
* Promote helper always writes ``+0.7``.
* Source label always matches the helper that wrote it.

The tests apply hypothesis's stateful strategies but do NOT mutate
external state — they share the ``applied_conn`` fixture and clean
the feedback table between examples.
"""

from __future__ import annotations

import math
import sqlite3

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.implicit_feedback import (
    record_implicit_archive,
    record_implicit_link,
    record_implicit_promote,
)

_SUPPORTED_SOURCE_TYPES = ("chunk", "decision", "theory", "insight", "capability")
_SUPPORTED_LINK_TARGETS = ("theory", "decision", "research_insight")


def _enabled_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_IMPLICIT_FEEDBACK_ENABLED="true",
    )


def _read_last_row(conn: sqlite3.Connection) -> tuple[str, float, str]:
    row = conn.execute(
        "SELECT source_type, usefulness, source FROM memory_usage_feedback "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    return str(row[0]), float(row[1]), str(row[2])


def _clear(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM memory_usage_feedback")


@given(
    strength=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    target_type=st.sampled_from(_SUPPORTED_LINK_TARGETS),
    target_id=st.text(min_size=1, max_size=20).filter(lambda s: s.strip() and "'" not in s),
)
@settings(
    max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_link_strength_always_in_unit_interval(
    applied_conn: sqlite3.Connection,
    strength: float,
    target_type: str,
    target_id: str,
) -> None:
    """For any finite input strength, the recorded weight is in [0, 1]."""
    _clear(applied_conn)
    settings_on = _enabled_settings()
    written = record_implicit_link(
        applied_conn,
        settings=settings_on,
        workspace_id="default",
        target_type=target_type,
        target_id=target_id,
        strength=strength,
    )
    if not written:
        # 0.0 / negative strengths skip silently, no row written.
        assert strength <= 0.0, "non-positive strength should be the only skip reason"
        return
    _, weight, source = _read_last_row(applied_conn)
    assert 0.0 <= weight <= 1.0
    assert not math.isnan(weight)
    assert source == "implicit_link"


@given(source_type=st.sampled_from(_SUPPORTED_SOURCE_TYPES))
@settings(
    max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_archive_always_negative_one(applied_conn: sqlite3.Connection, source_type: str) -> None:
    """Archive helper writes -1.0 regardless of operator-supplied source_type."""
    _clear(applied_conn)
    record_implicit_archive(
        applied_conn,
        settings=_enabled_settings(),
        workspace_id="default",
        source_type=source_type,
        source_id=f"x_{source_type}",
    )
    st_, weight, source = _read_last_row(applied_conn)
    assert st_ == source_type
    assert weight == -1.0
    assert source == "implicit_archive"


@given(source_type=st.sampled_from(_SUPPORTED_SOURCE_TYPES))
@settings(
    max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_promote_always_zero_point_seven(
    applied_conn: sqlite3.Connection, source_type: str
) -> None:
    """Promote helper writes +0.7 regardless of operator-supplied source_type."""
    _clear(applied_conn)
    record_implicit_promote(
        applied_conn,
        settings=_enabled_settings(),
        workspace_id="default",
        source_type=source_type,
        source_id=f"y_{source_type}",
    )
    st_, weight, source = _read_last_row(applied_conn)
    assert st_ == source_type
    assert weight == 0.7
    assert source == "implicit_promote"


@given(
    source_type=st.text(min_size=1, max_size=20).filter(lambda s: s not in _SUPPORTED_SOURCE_TYPES),
)
@settings(
    max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_unsupported_source_type_writes_nothing(
    applied_conn: sqlite3.Connection, source_type: str
) -> None:
    """Bogus source_type → helpers return False, no rows written."""
    _clear(applied_conn)
    settings_on = _enabled_settings()
    assert (
        record_implicit_archive(
            applied_conn,
            settings=settings_on,
            workspace_id="default",
            source_type=source_type,
            source_id="x",
        )
        is False
    )
    assert (
        record_implicit_promote(
            applied_conn,
            settings=settings_on,
            workspace_id="default",
            source_type=source_type,
            source_id="y",
        )
        is False
    )
    count = applied_conn.execute("SELECT COUNT(*) FROM memory_usage_feedback").fetchone()[0]
    assert count == 0
