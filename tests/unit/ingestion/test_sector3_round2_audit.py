"""Sector 3 Round-2 adversarial audit — regression locks.

A fresh adversarial agent audited the ingestion & writes layer:
  CRITICAL — episode summary + label bypassed redaction (stored
             cleartext + FTS-indexed)
  HIGH     — keyword redaction \\S+ stopped at the first space, so a
             quoted secret with spaces leaked its tail
  HIGH     — the trust gate ran only at candidate-write time, never
             re-checked at promotion (the actual privilege escalation)
  MEDIUM   — LLM-extractor output (subject/evidence/object) was stored
             without re-redaction
  MEDIUM   — episode dedup ran cosine on a degenerate vector with no
             NaN / zero-norm guard

This file locks each fix so a re-audit finds nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.models.enums import (
    EpisodeSource,
    MemoryCandidateKind,
    MemoryCandidateStatus,
    TrustLevel,
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_connection(tmp_path / "sector3.db")
    apply_migrations(conn)
    yield conn
    conn.close()


# ---------- CRITICAL: summary + label bypass redaction ----------


def test_episode_summary_and_label_are_redacted(db: sqlite3.Connection) -> None:
    """ingest_episode must redact summary + label, not just raw_text.
    A secret in either used to land in episodes.* and chunks_fts.summary
    cleartext."""
    from agent_memory_lite.ingestion.episode_pipeline import ingest_episode  # noqa: PLC0415
    from agent_memory_lite.models.episodes import EpisodeIn  # noqa: PLC0415

    result = ingest_episode(
        db,
        EpisodeIn(
            workspace_id="ws",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="ran the tests, all green",
            summary="api_key: sk-ant-secret-AAAA",
            label="token=sk-ant-label-BBBB",
        ),
    )
    # The persisted episode must carry redaction markers, not the secret.
    assert "sk-ant-secret-AAAA" not in (result.episode.summary or "")
    assert "<<REDACTED" in (result.episode.summary or "")
    assert "sk-ant-label-BBBB" not in (result.episode.label or "")
    assert "<<REDACTED" in (result.episode.label or "")
    # And the secret must not survive in chunks_fts.summary either.
    fts_summary = db.execute("SELECT summary FROM chunks_fts WHERE workspace_id = 'ws'").fetchone()
    assert fts_summary is not None
    assert "sk-ant-secret-AAAA" not in str(fts_summary[0] or "")


def test_episode_without_summary_label_still_ingests(db: sqlite3.Connection) -> None:
    """The redaction guard must tolerate the common None case."""
    from agent_memory_lite.ingestion.episode_pipeline import ingest_episode  # noqa: PLC0415
    from agent_memory_lite.models.episodes import EpisodeIn  # noqa: PLC0415

    result = ingest_episode(
        db,
        EpisodeIn(
            workspace_id="ws",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="no summary no label",
        ),
    )
    assert result.episode.summary is None
    assert result.episode.label is None


# ---------- HIGH: keyword redaction \\S+ quoted-value leak ----------


def test_quoted_secret_with_spaces_fully_redacted() -> None:
    """A quoted secret containing spaces used to leak its tail — \\S+
    stopped at the first space. Now the whole quoted value goes."""
    from agent_memory_lite.redaction import redact  # noqa: PLC0415

    for text in (
        'password = "my long secret phrase"',
        "api_key = 'spaced secret value'",
        'token: "value with several words"',
    ):
        out = redact(text).text
        # No fragment of the quoted value survives.
        for leaked in ("secret", "phrase", "value", "words"):
            assert leaked not in out, f"{leaked!r} leaked from {text!r}: {out!r}"


def test_unquoted_secret_does_not_over_redact_prose() -> None:
    """The fix must NOT swallow the rest of the line — prose after an
    unquoted value is kept."""
    from agent_memory_lite.redaction import redact  # noqa: PLC0415

    out = redact("the password: hunter2 and then more prose").text
    assert "hunter2" not in out
    assert "more prose" in out  # prose preserved


# ---------- HIGH: trust gate re-checked at promotion ----------


def _stored_candidate(*, kind: MemoryCandidateKind, trust: TrustLevel) -> object:
    from agent_memory_lite.models.candidates import StoredMemoryCandidate  # noqa: PLC0415

    return StoredMemoryCandidate(
        id="cand_1",
        workspace_id="ws",
        kind=kind,
        subject="do the thing",
        predicate="implies",
        object=None,
        evidence="some evidence text",
        confidence=0.9,
        importance=0.8,
        trust_level=trust,
        temporal={"observed_at": "2026-05-20", "valid_from": "2026-05-20"},
        write_targets=[],
        metadata={},
        source_episode_id="ep_1",
        status=MemoryCandidateStatus.NEW,
        created_at="2026-05-20T00:00:00+00:00",
        updated_at="2026-05-20T00:00:00+00:00",
    )


def test_promotion_rejects_untrusted_doc_decision(db: sqlite3.Connection) -> None:
    """An UNTRUSTED_DOC candidate of a promotable kind must be blocked at
    promote_to_target — even if it somehow reached PENDING status."""
    from agent_memory_lite.api.errors import ValidationError  # noqa: PLC0415
    from agent_memory_lite.ingestion.candidate_promotion import (  # noqa: PLC0415
        promote_to_target,
    )

    bad = _stored_candidate(kind=MemoryCandidateKind.DECISION, trust=TrustLevel.UNTRUSTED_DOC)
    with pytest.raises(ValidationError, match="trust gate"):
        promote_to_target(db, bad)  # type: ignore[arg-type]
    # Status enum sanity — keep the helper honest if the enum drifts.
    assert MemoryCandidateStatus.NEW.value == "new"


def test_promotion_allows_trusted_decision(db: sqlite3.Connection) -> None:
    """A user-asserted decision candidate still promotes fine — the gate
    only blocks the untrusted-doc class."""
    from agent_memory_lite.ingestion.candidate_promotion import (  # noqa: PLC0415
        promote_to_target,
    )

    # The promoted decision FK-references source_episode_id="ep_1";
    # seed that episode so the write succeeds.
    db.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES ('ep_1', 'ws', 'agent_action', 'evidence', '2026-05-20T00:00:00+00:00')"
    )
    db.commit()
    good = _stored_candidate(kind=MemoryCandidateKind.DECISION, trust=TrustLevel.USER_ASSERTED)
    target_type, target_id = promote_to_target(db, good)  # type: ignore[arg-type]
    assert target_type == "decision"
    assert target_id


# ---------- MEDIUM: LLM-extractor output re-redaction ----------


def test_llm_extractor_scrub_redacts_echoed_secret() -> None:
    """_scrub re-redacts a secret the LLM echoed into a candidate field."""
    from agent_memory_lite.extraction.llm_extractor import _scrub  # noqa: PLC0415

    out = _scrub("the api_key: sk-ant-leaked-CCCC is here")
    assert out is not None
    assert "sk-ant-leaked-CCCC" not in out
    assert "<<REDACTED" in out
    # None / empty pass through untouched.
    assert _scrub(None) is None
    assert _scrub("") == ""


# ---------- MEDIUM: episode dedup degenerate-vector guard ----------


class _ZeroVecProvider:
    """Embedding provider that returns an all-zero vector — the
    degenerate case that makes cosine NaN / collapse to 1.0."""

    name = "zero"
    dim = 4

    def embed_batch(self, texts: list[str], kind: str) -> list[np.ndarray]:
        return [np.zeros(4, dtype=np.float32) for _ in texts]


def test_embed_first_rejects_zero_norm_vector() -> None:
    """A zero-norm embedding must yield None so dedup is skipped — never
    wrongly collapse a distinct episode into a duplicate."""
    from agent_memory_lite.ingestion.episode_dedup import _embed_first  # noqa: PLC0415

    result = _embed_first("ws", "redacted-only text", _ZeroVecProvider())  # type: ignore[arg-type]
    assert result is None


class _NanVecProvider:
    name = "nan"
    dim = 4

    def embed_batch(self, texts: list[str], kind: str) -> list[np.ndarray]:
        return [np.array([float("nan"), 0.1, 0.2, 0.3], dtype=np.float32) for _ in texts]


def test_embed_first_rejects_non_finite_vector() -> None:
    """A NaN in the embedding must also yield None."""
    from agent_memory_lite.ingestion.episode_dedup import _embed_first  # noqa: PLC0415

    result = _embed_first("ws", "pathological text", _NanVecProvider())  # type: ignore[arg-type]
    assert result is None
