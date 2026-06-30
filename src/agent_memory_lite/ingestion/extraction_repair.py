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
    # Imported lazily: auto_promote pulls the extractor stack (Ollama client etc.),
    # which must not load on the cheap steady-state probe path.
    from agent_memory_lite.ingestion.auto_promote import auto_promote  # noqa: PLC0415

    pending = list_episodes_pending_extraction(conn, workspace_id, limit=limit)
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
