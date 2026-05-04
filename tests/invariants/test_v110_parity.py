"""Flag-off parity invariant for v1.10 correction-aware loop.

Locks the contract: with `MEMORY_CORRECTION_DETECT_ENABLED=false`,
the auto_promote pipeline runs without registering CorrectionExtractor,
so a user_correction-shaped episode produces zero CORRECTION
candidates. The flag is the only behavioral switch; nothing in the
data path leaks v1.10 behavior unless the flag is on.

Pair-checked with the active-flag path in
``tests/integration/test_correction_loop_e2e.py``.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion.auto_promote import _build_extractors


def _settings_with(correction_enabled: bool) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_CORRECTION_DETECT_ENABLED="true" if correction_enabled else "false",
    )


@pytest.fixture
def empty_conn() -> sqlite3.Connection:
    """A trivial conn so _build_extractors doesn't refuse the parameter."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def test_extractor_not_registered_when_flag_off(empty_conn: sqlite3.Connection) -> None:
    settings = _settings_with(correction_enabled=False)
    extractors = _build_extractors(settings, empty_conn)
    names = [e.name for e in extractors]
    assert "correction" not in names


def test_extractor_registered_when_flag_on(empty_conn: sqlite3.Connection) -> None:
    settings = _settings_with(correction_enabled=True)
    extractors = _build_extractors(settings, empty_conn)
    names = [e.name for e in extractors]
    assert "correction" in names


def test_extractor_skipped_when_no_conn() -> None:
    """Without a conn, the v1.10 extractor cannot resolve paired claims;
    _build_extractors omits it rather than raising."""
    settings = _settings_with(correction_enabled=True)
    extractors = _build_extractors(settings, conn=None)
    names = [e.name for e in extractors]
    assert "correction" not in names
