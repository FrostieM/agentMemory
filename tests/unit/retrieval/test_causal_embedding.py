"""v3.1 Vector 4 — embedding-based causal link derivation."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from agent_memory_lite.retrieval import causal_embedding as ce


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_CAUSAL_EMBEDDING_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_CAUSAL_EMBEDDING_THRESHOLD", raising=False)
    monkeypatch.delenv("MEMORY_CAUSAL_EMBEDDING_WINDOW", raising=False)


CANONICAL_ROOT = Path(__file__).resolve().parents[3] / "migrations" / "canonical"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Hybrid schema with canonical overlay — needs causal_links."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(tmp_path / "ce.db")
    apply_migrations(c)
    # causal_links lives in canonical/0008 — needs init + the table itself.
    c.executescript((CANONICAL_ROOT / "0001_init.sql").read_text(encoding="utf-8"))
    c.executescript((CANONICAL_ROOT / "0008_causal_links.sql").read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_decision(conn: sqlite3.Connection, did: str, body: str, updated_at: str) -> None:
    conn.execute(
        """INSERT INTO decisions (
            id, workspace_id, title, decision_text, rationale,
            status, valid_from, created_at, updated_at
        ) VALUES (?, 'ws', ?, ?, '', 'active', ?, ?, ?)""",
        (did, body, body, updated_at, updated_at, updated_at),
    )
    conn.commit()


class _FakeProvider:
    """Vector provider returning deterministic L2-normalized vectors.

    Each text gets a 3-D vector based on which 'topic' it mentions.
    Texts with the same topic → identical vectors → cosine=1.0.
    """

    name = "fake"
    dim = 3

    def embed_batch(self, texts: list[str], *, kind: str = "doc") -> np.ndarray:
        vecs = []
        for t in texts:
            if "kelly" in t.lower():
                vecs.append([1.0, 0.0, 0.0])
            elif "wal" in t.lower() or "sqlite" in t.lower():
                vecs.append([0.0, 1.0, 0.0])
            else:
                vecs.append([0.0, 0.0, 1.0])
        return np.array(vecs, dtype=np.float32)


def _patch_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``get_embedding_provider`` return our fake."""
    import agent_memory_lite.embeddings.factory as fac  # noqa: PLC0415

    monkeypatch.setattr(fac, "get_embedding_provider", lambda _s: _FakeProvider())


def test_enabled_by_default_returns_zero_on_empty_workspace(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Updated 2026-05-20: default flipped to True. Empty workspace
    has no decisions to derive from → returns 0 anyway."""
    assert ce.is_enabled() is True
    # Need to stub the embedding provider so the derive call doesn't
    # try to load real sentence_transformers on an empty workspace.
    _patch_provider(monkeypatch)
    out = ce.derive_workspace(conn, workspace_id="ws")
    assert out == 0


def test_env_flag_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator can opt out per workspace."""
    monkeypatch.setenv("MEMORY_CAUSAL_EMBEDDING_ENABLED", "false")
    assert ce.is_enabled() is False


def test_env_helpers_defaults() -> None:
    assert ce.similarity_threshold() == 0.75
    assert ce.window_size() == 20


def test_no_decisions_returns_zero(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_CAUSAL_EMBEDDING_ENABLED", "true")
    _patch_provider(monkeypatch)
    out = ce.derive_workspace(conn, workspace_id="ws")
    assert out == 0


def test_one_decision_returns_zero(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_CAUSAL_EMBEDDING_ENABLED", "true")
    _patch_provider(monkeypatch)
    _seed_decision(conn, "dec_only", "kelly sizing", "2026-01-01T00:00:00+00:00")
    assert ce.derive_workspace(conn, workspace_id="ws") == 0


def test_emits_link_when_decisions_semantically_similar(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two decisions about the same topic → cosine 1.0 → link emitted."""
    monkeypatch.setenv("MEMORY_CAUSAL_EMBEDDING_ENABLED", "true")
    _patch_provider(monkeypatch)
    _seed_decision(conn, "dec_kelly_old", "kelly sizing one", "2026-01-01T00:00:00+00:00")
    _seed_decision(conn, "dec_kelly_new", "kelly sizing two", "2026-03-01T00:00:00+00:00")
    n = ce.derive_workspace(conn, workspace_id="ws")
    assert n == 1
    row = conn.execute(
        "SELECT src_id, dst_id, relation, weight FROM causal_links "
        "WHERE workspace_id = 'ws' AND relation = 'semantically_similar_to'"
    ).fetchone()
    assert row is not None
    # rows[0] is most recent → "dec_kelly_new"; rows[1] is older →
    # "dec_kelly_old". Link goes earlier → later.
    assert row["src_id"] == "dec_kelly_old"
    assert row["dst_id"] == "dec_kelly_new"
    assert row["weight"] == pytest.approx(1.0)


def test_no_link_when_below_threshold(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two decisions about different topics → cosine 0 → no link."""
    monkeypatch.setenv("MEMORY_CAUSAL_EMBEDDING_ENABLED", "true")
    _patch_provider(monkeypatch)
    _seed_decision(conn, "dec_kelly", "kelly sizing", "2026-01-01T00:00:00+00:00")
    _seed_decision(conn, "dec_wal", "sqlite WAL mode", "2026-03-01T00:00:00+00:00")
    assert ce.derive_workspace(conn, workspace_id="ws") == 0


def test_idempotent(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running emits zero new links — UNIQUE constraint enforces."""
    monkeypatch.setenv("MEMORY_CAUSAL_EMBEDDING_ENABLED", "true")
    _patch_provider(monkeypatch)
    _seed_decision(conn, "dec_a", "kelly sizing one", "2026-01-01T00:00:00+00:00")
    _seed_decision(conn, "dec_b", "kelly sizing two", "2026-03-01T00:00:00+00:00")
    assert ce.derive_workspace(conn, workspace_id="ws") == 1
    assert ce.derive_workspace(conn, workspace_id="ws") == 0


def test_threshold_override(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stricter threshold (1.1) blocks even identical-topic pairs."""
    monkeypatch.setenv("MEMORY_CAUSAL_EMBEDDING_ENABLED", "true")
    monkeypatch.setenv("MEMORY_CAUSAL_EMBEDDING_THRESHOLD", "1.1")
    _patch_provider(monkeypatch)
    _seed_decision(conn, "dec_a", "kelly sizing", "2026-01-01T00:00:00+00:00")
    _seed_decision(conn, "dec_b", "kelly sizing", "2026-03-01T00:00:00+00:00")
    assert ce.derive_workspace(conn, workspace_id="ws") == 0


def test_workspace_isolation(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Decisions in ws_a don't pair with ws_b decisions."""
    monkeypatch.setenv("MEMORY_CAUSAL_EMBEDDING_ENABLED", "true")
    _patch_provider(monkeypatch)
    conn.execute(
        """INSERT INTO decisions (
            id, workspace_id, title, decision_text, rationale,
            status, valid_from, created_at, updated_at
        ) VALUES ('dec_alpha', 'alpha', 'kelly one', 'kelly one', '',
                  'active', ?, ?, ?)""",
        ("2026-01-01T00:00:00+00:00",) * 3,
    )
    conn.execute(
        """INSERT INTO decisions (
            id, workspace_id, title, decision_text, rationale,
            status, valid_from, created_at, updated_at
        ) VALUES ('dec_beta', 'beta', 'kelly two', 'kelly two', '',
                  'active', ?, ?, ?)""",
        ("2026-03-01T00:00:00+00:00",) * 3,
    )
    conn.commit()
    assert ce.derive_workspace(conn, workspace_id="alpha") == 0
    assert ce.derive_workspace(conn, workspace_id="beta") == 0


def test_failure_soft_when_provider_unavailable(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider import error → derive_workspace returns 0, no crash."""
    monkeypatch.setenv("MEMORY_CAUSAL_EMBEDDING_ENABLED", "true")
    _seed_decision(conn, "dec_a", "kelly one", "2026-01-01T00:00:00+00:00")
    _seed_decision(conn, "dec_b", "kelly two", "2026-03-01T00:00:00+00:00")

    import agent_memory_lite.embeddings.factory as fac  # noqa: PLC0415

    def _raise(_s: object) -> None:
        raise RuntimeError("embedding offline")

    monkeypatch.setattr(fac, "get_embedding_provider", _raise)
    assert ce.derive_workspace(conn, workspace_id="ws") == 0
