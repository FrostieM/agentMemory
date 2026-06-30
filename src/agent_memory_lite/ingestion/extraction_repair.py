"""Brain-pass drain for deferred episode extraction (Batch E).

The inverse complement of ``MEMORY_DEFER_EXTRACTION``: ``ingest_episode`` marks
an episode ``extraction_pending = 1`` instead of running ``auto_promote`` inline,
and this drains a bounded batch on the brain pass -- running the (slow,
Ollama-backed) extractor OFF the request thread and clearing the flag as each
episode completes. Mirrors ``vector_store.reindex.repair_missing_vectors`` (the
deferred-embedding healer).

Semantics: idempotent on retry. Each episode's candidate writes AND its
``extraction_pending`` clear run inside ONE transaction (``with_tx``;
``auto_promote``'s own inner ``with_tx`` becomes a SAVEPOINT under it), so they
commit atomically -- a failure or crash mid-episode rolls back THAT episode's
candidates AND leaves ``extraction_pending = 1``, so the next pass re-drains from
a clean slate instead of writing DUPLICATE candidates (matching the
deterministic-id upsert idempotency of ``repair_missing_vectors``). Per-episode
failure-soft: one poison episode is skipped, never aborting the batch.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.repositories.episodes_extraction_repo import (
    clear_episode_extraction_pending,
    list_episodes_pending_extraction,
)

_log = get_logger("ingestion.extraction_repair")


@dataclass(frozen=True, slots=True)
class ExtractionDrainResult:
    episodes_drained: int = 0
    candidates_written: int = 0


def drain_pending_extraction(
    conn: sqlite3.Connection, *, workspace_id: str, settings: Settings, limit: int
) -> ExtractionDrainResult:
    """Run the deferred extractor for up to ``limit`` pending episodes, clearing
    ``extraction_pending`` as each one completes. Per-episode failure-soft."""
    pending = list_episodes_pending_extraction(conn, workspace_id, limit=limit)
    if not pending:
        return ExtractionDrainResult()

    # M2 (global-audit 2026-06-30): the WHOLE POINT of deferring is the slow
    # Ollama extraction. If Ollama is the configured backend but unreachable right
    # now, draining would run the heuristic extractors only, clear
    # extraction_pending, and PERMANENTLY lose the Ollama-derived candidates -- the
    # episode is never re-drained. So probe first; when Ollama is expected and down,
    # skip this pass and LEAVE the flag set, so the next brain pass retries once
    # Ollama recovers. probe_ollama self-gates on ollama_probe_skip (a no-op when
    # Ollama is intentionally unused -> heuristic-only drain proceeds, nothing lost).
    # Mirrors repair_missing_vectors leaving the NULL marker when embedding is down.
    from agent_memory_lite.extraction.base import ExtractorUnavailableError  # noqa: PLC0415
    from agent_memory_lite.extraction.llm_extractor import probe_ollama  # noqa: PLC0415

    try:
        probe_ollama(settings)
    except ExtractorUnavailableError as exc:
        _log.warning(
            "deferred_extraction_skipped_ollama_unreachable", pending=len(pending), error=str(exc)
        )
        return ExtractionDrainResult()

    # Imported lazily: auto_promote pulls the extractor stack (Ollama client etc.),
    # which must not load on the cheap steady-state probe path.
    from agent_memory_lite.ingestion.auto_promote import auto_promote  # noqa: PLC0415

    drained = 0
    candidates = 0
    for episode in pending:
        try:
            # ATOMIC per episode: auto_promote's candidate writes (each its own
            # with_tx -> SAVEPOINT under this outer tx) AND clear_episode_extraction_pending
            # commit together or not at all. A failure/crash mid-episode rolls back
            # this episode's candidates and leaves extraction_pending = 1, so the
            # next pass re-drains from a clean slate -- no duplicate candidates.
            with with_tx(conn):
                stats = auto_promote(conn, episode, settings)
                clear_episode_extraction_pending(conn, episode.id)
        except Exception as exc:
            # Atomic rollback already undid any partial candidate writes; the flag
            # stays 1 so the next pass retries. Skip without aborting the batch.
            _log.warning("deferred_extraction_failed", episode_id=episode.id, error=str(exc))
            continue
        drained += 1
        candidates += stats.candidates_written
    return ExtractionDrainResult(episodes_drained=drained, candidates_written=candidates)
