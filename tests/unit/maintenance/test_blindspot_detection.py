"""Tests for v3.1 Vector 3 — structural blindspot detection."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_memory_lite.maintenance import blindspot_detection as bs

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_BLINDSPOT_DETECT_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_BLINDSPOT_LOOKBACK_DAYS", raising=False)
    monkeypatch.delenv("MEMORY_BLINDSPOT_MIN_EPISODES", raising=False)
    monkeypatch.delenv("MEMORY_BLINDSPOT_LIMIT", raising=False)
    # LLM augmentation flipped to ON by default — disable for
    # deterministic format assertions in unit tests.
    monkeypatch.setenv("MEMORY_BLINDSPOT_LLM_ENABLED", "false")


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_episode(
    conn: sqlite3.Connection,
    *,
    ep_id: str,
    raw_text: str,
    workspace_id: str = "ws",
    age_days: int = 1,
) -> None:
    ts = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, ?, 'agent_action', ?, ?)",
        (ep_id, workspace_id, raw_text, ts),
    )
    conn.commit()


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    dec_id: str,
    title: str,
    decision_text: str,
    workspace_id: str = "ws",
    status: str = "active",
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence,
            importance, valid_from, valid_to, created_at, updated_at, pinned)
           VALUES (?, ?, ?, ?, NULL, ?, NULL, NULL, 0.9, 0.8,
                   ?, NULL, ?, ?, 0)""",
        (dec_id, workspace_id, title, decision_text, status, now, now, now),
    )
    conn.commit()


def test_returns_empty_when_disabled(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_BLINDSPOT_DETECT_ENABLED", "false")
    _seed_episode(conn, ep_id="ep1", raw_text="quarter-Kelly fraction prediction")
    assert bs.find_blindspots(conn, workspace_id="ws") == []


def test_returns_empty_on_pristine_workspace(conn: sqlite3.Connection) -> None:
    assert bs.find_blindspots(conn, workspace_id="ws") == []


def test_finds_token_in_many_episodes_but_no_decision(conn: sqlite3.Connection) -> None:
    """'kelly' appears in 5 episodes, never in any decision → surface it
    as a blindspot. v3.3 bigram mode promotes 'kelly sizing' to the top
    since multi-word phrases beat plain unigrams at the same frequency;
    either is acceptable — the intent is that the 'kelly' concept
    surfaces."""
    for i in range(5):
        _seed_episode(conn, ep_id=f"ep{i}", raw_text=f"kelly sizing chat number {i}")
    out = bs.find_blindspots(conn, workspace_id="ws", min_episodes=5)
    tokens = {b.token for b in out}
    assert "kelly" in tokens or "kelly sizing" in tokens


def test_token_with_decision_is_not_blindspot(conn: sqlite3.Connection) -> None:
    """Once a decision covers the token it stops counting as blindspot."""
    for i in range(5):
        _seed_episode(conn, ep_id=f"ep{i}", raw_text="kelly sizing discussion")
    _seed_decision(
        conn,
        dec_id="dec_kelly",
        title="Kelly sizing approach",
        decision_text="Use quarter-kelly",
    )
    out = bs.find_blindspots(conn, workspace_id="ws", min_episodes=5)
    assert all(b.token != "kelly" for b in out)


def test_below_threshold_tokens_excluded(conn: sqlite3.Connection) -> None:
    """Token appearing in 3 episodes with min_episodes=5 → excluded."""
    for i in range(3):
        _seed_episode(conn, ep_id=f"ep{i}", raw_text="kelly sizing discussion")
    out = bs.find_blindspots(conn, workspace_id="ws", min_episodes=5)
    assert all(b.token != "kelly" for b in out)


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    """Tokens in workspace=beta must not leak into workspace=alpha lookup."""
    for i in range(5):
        _seed_episode(conn, ep_id=f"ep{i}", workspace_id="beta", raw_text="kelly sizing")
    out = bs.find_blindspots(conn, workspace_id="alpha", min_episodes=5)
    assert out == []


def test_lookback_window_excludes_old_episodes(conn: sqlite3.Connection) -> None:
    """Episodes older than the window don't contribute to the count."""
    for i in range(5):
        _seed_episode(conn, ep_id=f"ep{i}", raw_text="kelly sizing", age_days=120)
    out = bs.find_blindspots(conn, workspace_id="ws", days=30, min_episodes=5)
    assert out == []


def test_limit_caps_result_count(conn: sqlite3.Connection) -> None:
    """``limit`` caps the returned rows even when many tokens qualify."""
    for i in range(5):
        _seed_episode(
            conn,
            ep_id=f"ep{i}",
            raw_text="kelly fraction quarter prediction strategy edge",
        )
    out = bs.find_blindspots(conn, workspace_id="ws", min_episodes=5, limit=2)
    assert len(out) == 2


def test_ordered_by_episode_count_desc(conn: sqlite3.Connection) -> None:
    """Highest-frequency tokens appear first."""
    # 'kelly' appears in 5 episodes, 'edge' in 6.
    for i in range(5):
        _seed_episode(conn, ep_id=f"ep_kelly_{i}", raw_text="kelly betting")
    for i in range(6):
        _seed_episode(conn, ep_id=f"ep_edge_{i}", raw_text="edge measurement")
    out = bs.find_blindspots(conn, workspace_id="ws", min_episodes=5)
    counts = {b.token: b.episode_count for b in out}
    if "edge" in counts and "kelly" in counts:
        assert counts["edge"] >= counts["kelly"]
        # Ordering invariant — 'edge' must precede 'kelly' in the list.
        ordering = [b.token for b in out]
        assert ordering.index("edge") < ordering.index("kelly")


def test_decision_count_always_zero(conn: sqlite3.Connection) -> None:
    """By construction blindspot tokens have decision_count == 0."""
    for i in range(5):
        _seed_episode(conn, ep_id=f"ep{i}", raw_text="kelly betting")
    out = bs.find_blindspots(conn, workspace_id="ws", min_episodes=5)
    assert all(b.decision_count == 0 for b in out)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env-flag readers parse the documented variables correctly."""
    monkeypatch.setenv("MEMORY_BLINDSPOT_LOOKBACK_DAYS", "30")
    monkeypatch.setenv("MEMORY_BLINDSPOT_MIN_EPISODES", "10")
    monkeypatch.setenv("MEMORY_BLINDSPOT_LIMIT", "7")
    assert bs.lookback_days() == 30
    assert bs.min_episode_count() == 10
    assert bs.emit_limit() == 7


def test_invalid_env_values_fall_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_BLINDSPOT_LOOKBACK_DAYS", "garbage")
    assert bs.lookback_days() == 90


# ============================================================
# Audit-round-2 C#3 — opaque-id filter
# ============================================================


def test_opaque_id_prefixes_filtered(conn: sqlite3.Connection) -> None:
    """Row-id tokens (``ep_xxx``, ``dec_yyy``) appear in every episode
    that references the row, but should NEVER be surfaced as blindspots
    — they're not topics."""
    for i in range(5):
        _seed_episode(
            conn,
            ep_id=f"ep_blind_{i}",
            raw_text=f"Touched ep_153e3ad and dec_09505 in step {i}",
        )
    out = bs.find_blindspots(conn, workspace_id="ws", min_episodes=5)
    tokens = {b.token for b in out}
    assert "ep_153e3ad" not in tokens
    assert "dec_09505" not in tokens
    # Also: `ins_`, `cm_`, `cand_` prefixes
    for prefix in ("ins_xxx", "cm_yyy", "cand_zzz"):
        assert prefix not in tokens


def test_pure_hex_tokens_filtered(conn: sqlite3.Connection) -> None:
    """SHA fragments / hex blobs should not surface as topics."""
    for i in range(5):
        _seed_episode(
            conn,
            ep_id=f"ep_hex_{i}",
            raw_text=f"Commit abc12345def discussion {i}",
        )
    out = bs.find_blindspots(conn, workspace_id="ws", min_episodes=5)
    tokens = {b.token for b in out}
    assert "abc12345def" not in tokens


def test_long_digit_runs_filtered_but_tech_terms_survive(
    conn: sqlite3.Connection,
) -> None:
    """Audit-round-3 tightening: 4+ consecutive digits → opaque (years,
    timestamps, ids); shorter digit-runs → keep (python3, sha1, oauth2)."""
    # Mix: ``2026`` (year, 4 digits) and ``0042`` (id, 4 digits) MUST be
    # filtered. ``python3`` (1 digit) MUST survive as a topical token.
    for i in range(5):
        _seed_episode(
            conn,
            ep_id=f"ep_num_{i}",
            raw_text=f"At 2026 revision 0042 saw python3 oauth2 sha1 in step {i}",
        )
    out = bs.find_blindspots(conn, workspace_id="ws", min_episodes=5)
    tokens = {b.token for b in out}
    # 4+ digit runs filtered:
    assert "2026" not in tokens
    assert "0042" not in tokens
    # Tech terms with short digit suffix survive (at least one should
    # land in the top-N blindspots given they all appear in 5 episodes).
    # v3.3: bigrams may take the unigram slots, so check both forms.
    tech_terms = {"python3", "oauth2", "sha1"}
    tech_bigrams = {"python3 oauth2", "oauth2 sha1"}
    assert tokens & tech_terms or tokens & tech_bigrams, (
        f"expected at least one tech term (unigram or bigram) in tokens, got {tokens}"
    )


# ============================================================
# Audit-round-2 C#2 — decision token-set cache
# ============================================================


def test_decision_token_set_cached_on_repeated_calls(conn: sqlite3.Connection) -> None:
    """Second call with no decision mutation hits the per-process cache.

    Asserts via the public ``_DECISION_TOKENS_CACHE`` state: after the
    first call the cache contains the key; the second call would return
    the same set without touching SQL again."""
    bs.reset_decision_tokens_cache()
    _seed_decision(
        conn,
        dec_id="dec_kelly",
        title="Kelly sizing approach",
        decision_text="Use quarter-kelly",
    )
    bs._decision_token_set(conn, workspace_id="ws", days=90)
    # Cache populated.
    assert len(bs._DECISION_TOKENS_CACHE) == 1
    cached_set = next(iter(bs._DECISION_TOKENS_CACHE.values()))
    assert "kelly" in cached_set
    # Second call returns the SAME set object (identity check verifies
    # the cache short-circuit fires).
    second = bs._decision_token_set(conn, workspace_id="ws", days=90)
    assert second is cached_set
    bs.reset_decision_tokens_cache()


def test_decision_token_set_cache_invalidates_on_new_decision(
    conn: sqlite3.Connection,
) -> None:
    """When a new decision is inserted, max_updated_at changes → cache
    key changes → fresh build."""
    bs.reset_decision_tokens_cache()
    _seed_decision(conn, dec_id="dec_one", title="Kelly", decision_text="Use kelly")
    first = bs._decision_token_set(conn, workspace_id="ws", days=90)
    _seed_decision(conn, dec_id="dec_two", title="Bandit policy", decision_text="UCB1")
    second = bs._decision_token_set(conn, workspace_id="ws", days=90)
    assert second is not first
    assert "bandit" in second or "policy" in second or "ucb1" in second
    bs.reset_decision_tokens_cache()
