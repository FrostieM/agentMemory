"""v3.3 last-mile — workspace-learned stopwords for V3 blindspots."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.maintenance.blindspot_learned_stops import (
    is_enabled,
    learn_workspace_stops,
    min_corpus_size,
    reset_cache,
    threshold,
)

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("MEMORY_BLINDSPOT_LEARNED_STOPS_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_BLINDSPOT_LEARNED_STOPS_THRESHOLD", raising=False)
    monkeypatch.delenv("MEMORY_BLINDSPOT_LEARNED_STOPS_MIN_EPISODES", raising=False)
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_episode(conn: sqlite3.Connection, *, idx: int, text: str, ws: str = "ws") -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    ts = (datetime.now(UTC) - timedelta(hours=idx)).isoformat()
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, ?, 'agent_action', ?, ?)",
        (f"ep_{ws}_{idx}", ws, text, ts),
    )
    conn.commit()


def test_defaults() -> None:
    assert is_enabled() is True
    assert threshold() == pytest.approx(0.40)
    assert min_corpus_size() == 30


def test_returns_empty_when_corpus_too_small(conn: sqlite3.Connection) -> None:
    """Below ``min_corpus_size`` (default 30) → no learning possible."""
    for i in range(10):
        _seed_episode(conn, idx=i, text="universal_word here always")
    stops = learn_workspace_stops(conn, workspace_id="ws", lookback_days=90)
    assert stops == frozenset()


def test_high_frequency_token_becomes_stopword(conn: sqlite3.Connection) -> None:
    """A token in 50% of episodes (above 40% threshold) → stopword."""
    # 40 episodes, 'common' appears in 30 of them (75%), 'rare' in 5.
    for i in range(30):
        _seed_episode(conn, idx=i, text="common token shows up")
    for i in range(30, 40):
        _seed_episode(conn, idx=i, text="rare cousin different content")
    stops = learn_workspace_stops(conn, workspace_id="ws", lookback_days=90)
    assert "common" in stops
    assert "rare" not in stops


def test_threshold_respected(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Raising the threshold lets more tokens through."""
    monkeypatch.setenv("MEMORY_BLINDSPOT_LEARNED_STOPS_THRESHOLD", "0.80")
    reset_cache()
    # 30 episodes, 'frequent' in 15 (50%). With threshold 0.80, this is NOT a stop.
    for i in range(30):
        text = "frequent topic" if i < 15 else "different content"
        _seed_episode(conn, idx=i, text=text)
    stops = learn_workspace_stops(conn, workspace_id="ws", lookback_days=90)
    assert "frequent" not in stops


def test_disabled_returns_empty(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_BLINDSPOT_LEARNED_STOPS_ENABLED", "false")
    reset_cache()
    for i in range(40):
        _seed_episode(conn, idx=i, text="universal word")
    stops = learn_workspace_stops(conn, workspace_id="ws", lookback_days=90)
    assert stops == frozenset()


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    """A common token in workspace A must not become a stop in B."""
    for i in range(40):
        _seed_episode(conn, idx=i, text="alphax token everywhere", ws="ws_a")
    for i in range(40):
        _seed_episode(conn, idx=i + 100, text="betax different content", ws="ws_b")
    stops_a = learn_workspace_stops(conn, workspace_id="ws_a", lookback_days=90)
    stops_b = learn_workspace_stops(conn, workspace_id="ws_b", lookback_days=90)
    assert "alphax" in stops_a
    assert "alphax" not in stops_b
    assert "betax" in stops_b
    assert "betax" not in stops_a


def test_cache_hit_skips_recomputation(conn: sqlite3.Connection) -> None:
    """Second call within the same day cache key returns same set."""
    for i in range(40):
        _seed_episode(conn, idx=i, text="alphax token bridge here")
    a = learn_workspace_stops(conn, workspace_id="ws", lookback_days=90)
    b = learn_workspace_stops(conn, workspace_id="ws", lookback_days=90)
    assert a is b  # exact same object due to cache hit


def test_failure_soft_when_table_missing() -> None:
    """Pre-migration DB → empty stopword set, not an exception."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        stops = learn_workspace_stops(c, workspace_id="ws", lookback_days=90)
        assert stops == frozenset()
    finally:
        c.close()


def test_bigrams_included_in_stops(conn: sqlite3.Connection) -> None:
    """Common bigrams ('compiled test') also become stopwords when
    they exceed the threshold — so the v3.3 bigram tokenizer's output
    doesn't slip past the learned filter."""
    for i in range(30):
        _seed_episode(conn, idx=i, text="compiled test passed today")
    stops = learn_workspace_stops(conn, workspace_id="ws", lookback_days=90)
    # Both unigrams and the bigram should land in the stop set.
    assert "compiled" in stops
    assert "compiled test" in stops
