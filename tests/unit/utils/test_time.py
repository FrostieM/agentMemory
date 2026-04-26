from __future__ import annotations

from datetime import UTC, datetime

from agent_memory_lite.utils.time import (
    iso_now,
    parse_iso,
    reset_now_provider,
    set_now_provider,
)


def test_iso_now_round_trips() -> None:
    s = iso_now()
    parsed = parse_iso(s)
    assert parsed.tzinfo is not None


def test_parse_iso_assumes_utc_when_naive() -> None:
    parsed = parse_iso("2026-04-26T12:00:00")
    assert parsed.tzinfo is UTC


def test_now_provider_is_overridable() -> None:
    fixed = datetime(2026, 4, 26, 0, 0, 0, tzinfo=UTC)
    set_now_provider(lambda: fixed)
    try:
        assert iso_now() == fixed.isoformat()
    finally:
        reset_now_provider()
