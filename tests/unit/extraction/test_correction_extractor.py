"""Unit tests for v1.10 CorrectionExtractor."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from agent_memory_lite.extraction.correction_extractor import CorrectionExtractor
from agent_memory_lite.models.enums import EpisodeSource, MemoryCandidateKind, TrustLevel
from agent_memory_lite.models.episodes import Episode


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """In-memory SQLite with a minimal episodes table for claim resolution.

    The extractor only needs to look up `raw_text` from the episodes
    table by id, so we don't apply the full migration set.
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE episodes (
            id TEXT PRIMARY KEY,
            raw_text TEXT NOT NULL,
            workspace_id TEXT NOT NULL DEFAULT 'default',
            metadata_json TEXT NOT NULL DEFAULT '{"kind": "correction_target"}'
        )
        """
    )
    yield c
    c.close()


def _episode(
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
    trust: TrustLevel = TrustLevel.USER_ASSERTED,
) -> Episode:
    return Episode(
        id="ep_user_correction",
        workspace_id="default",
        session_id="session_x",
        task_id=None,
        source_type=EpisodeSource.USER_MESSAGE,
        raw_text=text,
        summary=None,
        trust_level=trust,
        importance=0.7,
        confidence=1.0,
        created_at="2026-05-04T12:00:00Z",
        metadata=metadata or {},
    )


def _seed_claim(conn: sqlite3.Connection, claim_id: str, text: str) -> None:
    conn.execute("INSERT INTO episodes (id, raw_text) VALUES (?, ?)", (claim_id, text))


def test_no_correction_metadata_yields_no_candidates(conn: sqlite3.Connection) -> None:
    extractor = CorrectionExtractor(conn)
    # Episode without metadata.kind=user_correction is silently ignored.
    candidates = extractor.extract(_episode("нет, ты неправ", metadata={"kind": "agent_action"}))
    assert candidates == []


def test_correction_with_paired_claim_emits_candidate(
    conn: sqlite3.Connection,
) -> None:
    claim_text = "implicit feedback in copyBot is broken because audit_log shows zero feedback rows"
    _seed_claim(conn, "ep_claim_1", claim_text)
    extractor = CorrectionExtractor(conn)
    correction = "нет, MCP just started after 1.1.0; pre-release audit can't have triggered hooks"
    candidates = extractor.extract(
        _episode(
            correction,
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": "ep_claim_1",
            },
        )
    )
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.kind == MemoryCandidateKind.CORRECTION
    assert cand.confidence >= 0.5
    assert cand.subject.startswith("Verify before claiming:")
    assert cand.metadata["agent_claim_episode_id"] == "ep_claim_1"
    assert claim_text[:80] in cand.evidence
    assert "behavior_instruction" in cand.write_targets


def test_short_user_correction_filtered(conn: sqlite3.Connection) -> None:
    _seed_claim(conn, "ep_claim_1", "x" * 80)
    extractor = CorrectionExtractor(conn, min_user_len=30)
    candidates = extractor.extract(
        _episode(
            "нет.",
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": "ep_claim_1",
            },
        )
    )
    assert candidates == []


def test_short_agent_claim_filtered(conn: sqlite3.Connection) -> None:
    _seed_claim(conn, "ep_claim_1", "ok")  # 2 chars; below 50
    extractor = CorrectionExtractor(conn, min_agent_len=50)
    candidates = extractor.extract(
        _episode(
            "нет, это не так, потому что timestamps смещены и не должны были сработать",
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": "ep_claim_1",
            },
        )
    )
    assert candidates == []


def test_missing_claim_target_yields_no_candidate(conn: sqlite3.Connection) -> None:
    extractor = CorrectionExtractor(conn)
    candidates = extractor.extract(
        _episode(
            "нет, это не так, я проверил даты и всё указывает на другое",
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": "ep_does_not_exist",
            },
        )
    )
    assert candidates == []


def test_agreement_phrases_filtered_by_pattern_layer(
    conn: sqlite3.Connection,
) -> None:
    # min_user_len lowered so length isn't the gate; agreement filter is.
    _seed_claim(conn, "ep_claim_1", "x" * 80)
    extractor = CorrectionExtractor(conn, min_user_len=5)
    candidates = extractor.extract(
        _episode(
            "нет проблем",
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": "ep_claim_1",
            },
        )
    )
    assert candidates == []


def test_carries_episode_trust_level(conn: sqlite3.Connection) -> None:
    _seed_claim(conn, "ep_claim_1", "x" * 80)
    extractor = CorrectionExtractor(conn)
    candidates = extractor.extract(
        _episode(
            "нет, неправильно — ты не учёл версионную дату релиза 1.1.0",
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": "ep_claim_1",
            },
            trust=TrustLevel.USER_ASSERTED,
        )
    )
    assert candidates
    assert candidates[0].trust_level == TrustLevel.USER_ASSERTED


def test_throttle_rejects_after_daily_cap(conn: sqlite3.Connection) -> None:
    """When ``memory_candidates(kind='correction')`` rows for today reach
    ``max_per_day``, the extractor stops emitting until tomorrow."""
    # Add memory_candidates table with the columns the throttle reads.
    conn.execute(
        """
        CREATE TABLE memory_candidates (
            id TEXT, workspace_id TEXT, kind TEXT, created_at TEXT
        )
        """
    )
    today = datetime.now(UTC).strftime("%Y-%m-%dT12:00:00+00:00")
    yesterday = "2026-05-03T12:00:00+00:00"
    # Pre-seed: 2 rows today (cap=2), 1 yesterday (should not count).
    conn.executemany(
        "INSERT INTO memory_candidates (id, workspace_id, kind, created_at) "
        "VALUES (?, 'default', 'correction', ?)",
        [("c1", today), ("c2", today), ("c3", yesterday)],
    )

    _seed_claim(conn, "ep_claim_1", "x" * 80)
    extractor = CorrectionExtractor(conn, max_per_day=2)
    candidates = extractor.extract(
        _episode(
            "нет, неправильно — это не так, проверь даты в audit_log",
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": "ep_claim_1",
            },
        )
    )
    assert candidates == []  # throttled out


def test_throttle_isolates_workspaces(conn: sqlite3.Connection) -> None:
    """Throttle must count today's corrections per-workspace.

    Workspace A at cap should NOT block workspace B's corrections.
    Without this guarantee a chatty workspace would silently throttle
    every other workspace too.
    """
    conn.execute(
        """
        CREATE TABLE memory_candidates (
            id TEXT, workspace_id TEXT, kind TEXT, created_at TEXT
        )
        """
    )
    today = datetime.now(UTC).strftime("%Y-%m-%dT12:00:00+00:00")
    # Workspace A is at cap (cap=2). Workspace B has zero rows.
    conn.executemany(
        "INSERT INTO memory_candidates (id, workspace_id, kind, created_at) "
        "VALUES (?, ?, 'correction', ?)",
        [("ca1", "ws_a", today), ("ca2", "ws_a", today)],
    )

    # Seed two distinct claim episodes, one per workspace
    conn.executemany(
        "INSERT INTO episodes (id, raw_text, workspace_id) VALUES (?, ?, ?)",
        [("ep_a", "x" * 80, "ws_a"), ("ep_b", "y" * 80, "ws_b")],
    )
    extractor = CorrectionExtractor(conn, max_per_day=2)

    def _correction_episode(ep_id: str, ws: str, claim_id: str) -> Episode:
        return Episode(
            id=ep_id,
            workspace_id=ws,
            session_id=None,
            task_id=None,
            source_type=EpisodeSource.USER_MESSAGE,
            raw_text="нет, неправильно — это не так, проверь даты в audit_log",
            summary=None,
            trust_level=TrustLevel.USER_ASSERTED,
            importance=0.7,
            confidence=1.0,
            created_at="2026-05-04T12:00:00Z",
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": claim_id,
            },
        )

    # ws_a is at cap → rejected
    a_candidates = extractor.extract(_correction_episode("ep_corr_a", "ws_a", "ep_a"))
    assert a_candidates == [], "workspace at cap should reject"
    # ws_b has zero corrections today → must succeed
    b_candidates = extractor.extract(_correction_episode("ep_corr_b", "ws_b", "ep_b"))
    assert len(b_candidates) == 1, (
        f"workspace_b cap is independent of workspace_a; expected 1 candidate, "
        f"got {len(b_candidates)}"
    )


def test_throttle_allows_when_below_cap(conn: sqlite3.Connection) -> None:
    """When today's count is below max_per_day, the extractor still emits."""
    conn.execute(
        """
        CREATE TABLE memory_candidates (
            id TEXT, workspace_id TEXT, kind TEXT, created_at TEXT
        )
        """
    )
    today = datetime.now(UTC).strftime("%Y-%m-%dT12:00:00+00:00")
    # 1 row today, cap = 5 — should still emit.
    conn.execute(
        "INSERT INTO memory_candidates (id, workspace_id, kind, created_at) "
        "VALUES ('c1', 'default', 'correction', ?)",
        (today,),
    )

    _seed_claim(conn, "ep_claim_1", "x" * 80)
    extractor = CorrectionExtractor(conn, max_per_day=5)
    candidates = extractor.extract(
        _episode(
            "нет, неправильно — это не так, проверь даты в audit_log",
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": "ep_claim_1",
            },
        )
    )
    assert len(candidates) == 1


def test_namespaced_correction_role_field_works_alone(conn: sqlite3.Connection) -> None:
    """Backward-compat invariant: a future migration that removes the
    legacy ``metadata.kind`` field must not break v1.10. The
    ``correction_role`` namespaced marker is sufficient on its own.
    Mirror test for user_correction below.
    """
    conn.execute(
        "INSERT INTO episodes (id, raw_text, workspace_id, metadata_json) "
        "VALUES (?, ?, 'default', '{\"correction_role\": \"claim\"}')",
        ("ep_only_namespaced_claim", "x" * 80),
    )
    extractor = CorrectionExtractor(conn)
    candidates = extractor.extract(
        _episode(
            "нет, это не так, проверь даты в audit_log по дате релиза",
            metadata={
                # Legacy ``kind`` deliberately absent; only the
                # namespaced role is set.
                "correction_role": "user_correction",
                "correction_target_episode_id": "ep_only_namespaced_claim",
            },
        )
    )
    assert len(candidates) == 1, (
        "v1.10 must accept episodes carrying ONLY the namespaced "
        "correction_role field; legacy metadata.kind is optional"
    )


def test_provenance_check_blocks_unmarked_episode(conn: sqlite3.Connection) -> None:
    """SECURITY: even within the same workspace, an episode that wasn't
    ingested as ``metadata.kind="correction_target"`` must NOT be
    resolvable as a claim. Otherwise an attacker could forge a
    correction_target_episode_id pointing at any same-workspace
    episode (e.g. a sensitive past episode) and have its text leaked
    into the candidate's evidence field."""
    conn.execute(
        "INSERT INTO episodes (id, raw_text, workspace_id, metadata_json) "
        "VALUES (?, ?, 'default', '{\"kind\": \"agent_action\"}')",
        ("ep_not_a_claim", "x" * 80),
    )
    extractor = CorrectionExtractor(conn)
    candidates = extractor.extract(
        _episode(
            "нет, неправильно — это не так, проверь ещё раз",
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": "ep_not_a_claim",
            },
        )
    )
    assert candidates == [], (
        "claim must be rejected when source episode lacks metadata.kind='correction_target'"
    )


def test_workspace_isolation_blocks_foreign_claim(conn: sqlite3.Connection) -> None:
    """SECURITY: a forged correction_target_episode_id pointing at an
    episode in workspace A must NOT resolve when the user_correction
    episode lives in workspace B. Without the workspace_id filter, the
    extractor would leak claim text from a foreign workspace into a
    behavior_instruction in the calling workspace."""
    secret_text = "secret content from workspace A that workspace B must not see"
    conn.execute(
        "INSERT INTO episodes (id, raw_text, workspace_id, metadata_json) "
        "VALUES (?, ?, 'workspace_A', '{\"kind\": \"correction_target\"}')",
        ("ep_foreign_secret", secret_text),
    )

    extractor = CorrectionExtractor(conn)
    # Episode is in workspace_B but tries to reference workspace_A's episode
    foreign_episode = Episode(
        id="ep_user_correction_b",
        workspace_id="workspace_B",
        session_id=None,
        task_id=None,
        source_type=EpisodeSource.USER_MESSAGE,
        raw_text="нет, неправильно — то что ты сказал не подтверждается данными",
        summary=None,
        trust_level=TrustLevel.USER_ASSERTED,
        importance=0.7,
        confidence=1.0,
        created_at="2026-05-04T12:00:00Z",
        metadata={
            "kind": "user_correction",
            "correction_target_episode_id": "ep_foreign_secret",
        },
    )
    candidates = extractor.extract(foreign_episode)
    assert candidates == [], (
        "extractor must refuse to resolve a claim from a foreign workspace; "
        "leaking foreign episode text would be a data exfiltration vector"
    )


def test_subject_is_distilled_one_line(conn: sqlite3.Connection) -> None:
    _seed_claim(conn, "ep_claim_1", "x" * 80)
    extractor = CorrectionExtractor(conn)
    candidates = extractor.extract(
        _episode(
            "нет, это неверно. Нужно проверять timestamp перед тем как делать выводы.",
            metadata={
                "kind": "user_correction",
                "correction_target_episode_id": "ep_claim_1",
            },
        )
    )
    assert candidates
    subject = candidates[0].subject
    assert "\n" not in subject
    assert len(subject) <= 200
    assert subject.startswith("Verify before claiming:")
