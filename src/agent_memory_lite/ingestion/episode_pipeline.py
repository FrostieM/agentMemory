"""End-to-end episode write path.

Steps:
1. Redact secrets in the raw text.
2. Save the episode (with the redacted text as `raw_text`).
3. Create a single episode-kind chunk that mirrors the redacted text.
4. Insert the FTS row for that chunk.
5. Append an audit log entry.
6. (Optional) Embed the chunk and upsert into the vector store.

Persistence helpers live in ``episode_persist.py``. When
``embedding_provider`` is supplied, dimension drift is checked first
via ``pin_or_check``. Vector upsert happens *after* the SQLite
transaction commits; a vector-store failure logs a warning but does
not roll the chunk back. Run ``scripts/reindex_vectors.py`` to repair
drift.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.dimension_check import pin_or_check
from agent_memory_lite.ingestion.episode_dedup import maybe_dedup
from agent_memory_lite.ingestion.episode_persist import embed_and_upsert, persist
from agent_memory_lite.ingestion.maintenance_writer import write_maintenance_event
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.chunks import Chunk
from agent_memory_lite.models.enums import MaintenanceSeverity
from agent_memory_lite.models.episodes import Episode, EpisodeIn
from agent_memory_lite.models.maintenance import MaintenanceEventIn
from agent_memory_lite.redaction import redact
from agent_memory_lite.repositories.chunks_repo import get_chunk, set_chunk_embedding_id
from agent_memory_lite.repositories.episodes_repo import get_episode
from agent_memory_lite.repositories.vector_metadata_repo import upsert_vector_index_metadata
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

_log = get_logger("ingestion.episode_pipeline")


@dataclass(frozen=True, slots=True)
class EpisodeIngestResult:
    episode: Episode
    chunk: Chunk
    redacted_kinds: list[str]
    embedded: bool
    auto_promoted_decisions: int = 0
    auto_promoted_rules: int = 0
    auto_promoted_core: int = 0
    candidates_written: int = 0
    # Embedding dedup: when MEMORY_EPISODE_DEDUP_ENABLED=1 surfaces a
    # near-duplicate, the pipeline returns the existing episode + its
    # chunk and sets these two fields. Callers can detect a duplicate
    # by checking ``was_duplicate``; ``duplicate_similarity`` carries
    # the cosine score for telemetry.
    was_duplicate: bool = False
    duplicate_similarity: float = 0.0


def _redact_metadata(value: object) -> object:
    """Recursively redact secret-shaped substrings in a metadata value.

    Round-2 re-audit: ``EpisodeIn.metadata`` was persisted cleartext —
    only raw_text / summary / label were redacted. metadata is written
    verbatim to ``episodes.metadata_json``, returned by ``get_episode``
    / ``list_recent_episodes``, and rendered in the UI, so a secret in
    a metadata value (``metadata={"note": "key: sk-ant-..."}``) leaked.
    The redaction pass was field-enumerated, not recursive — this walks
    the nested dict/list structure. Non-secret control fields
    (``correction_role`` etc.) are untouched: ``redact`` only replaces
    secret-shaped spans, leaving plain values exactly as they were.

    Round-2 final verification: dict KEYS are redacted too, not just
    values — a secret used as a metadata key (an odd but possible
    shape) would otherwise survive. A normal key like ``"note"`` does
    not match any secret pattern, so it passes through unchanged.
    """
    if isinstance(value, str):
        return redact(value).text if value else value
    if isinstance(value, dict):
        return {
            (redact(k).text if isinstance(k, str) and k else k): _redact_metadata(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_metadata(v) for v in value]
    return value


def ingest_episode(
    conn: sqlite3.Connection,
    episode_in: EpisodeIn,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    auto_promote_settings: Settings | None = None,
) -> EpisodeIngestResult:
    redacted = redact(episode_in.raw_text)
    # Round-2 audit (CRITICAL): only raw_text used to be redacted.
    # ``summary`` and ``label`` were written verbatim to
    # episodes.summary / episodes.label AND mirrored onto the chunk +
    # chunks_fts (insert_chunk_fts indexes summary). A secret placed in
    # summary or label — e.g. ingest_episode(summary="key: sk-ant-...")
    # — landed in SQLite cleartext and FTS-indexed. Redact them on the
    # same pass. ``redact`` requires a non-None str, so guard the
    # optional fields; an empty string is already a no-op.
    # Round-2 re-audit: metadata also bypassed redaction — redact it too
    # (recursively, since it is a free-form dict).
    redacted_summary = redact(episode_in.summary).text if episode_in.summary else episode_in.summary
    redacted_label = redact(episode_in.label).text if episode_in.label else episode_in.label
    redacted_metadata = _redact_metadata(episode_in.metadata)
    if (
        redacted_summary != episode_in.summary
        or redacted_label != episode_in.label
        or redacted_metadata != episode_in.metadata
    ):
        episode_in = episode_in.model_copy(
            update={
                "summary": redacted_summary,
                "label": redacted_label,
                "metadata": redacted_metadata,
            }
        )

    if embedding_provider is not None:
        pin_or_check(conn, episode_in.workspace_id, embedding_provider)

    # v1.10: bypass embedding dedup for episodes that are part of a
    # correction pair. The pair is meaningful as an EVENT (the user
    # corrected the agent again) even when the text is near-identical
    # to a previous correction; deduping here would silently drop the
    # second candidate and the operator never sees the recurring
    # mistake. Both the claim ("correction_role"="claim") and the
    # follow-up ("correction_role"="user_correction") get the bypass.
    metadata = episode_in.metadata or {}
    correction_role = str(metadata.get("correction_role") or "")
    legacy_kind = str(metadata.get("kind") or "")
    is_correction_pair = correction_role in {"claim", "user_correction"} or legacy_kind in {
        "correction_target",
        "user_correction",
    }

    # Embedding-based dedup: opt-in via MEMORY_EPISODE_DEDUP_ENABLED.
    # When the new redacted text near-matches an existing chunk in
    # the same workspace, return that chunk's episode rather than
    # writing a low-information duplicate. Skipped when the
    # provider/store/settings aren't both available, or when the
    # episode is part of a v1.10 correction pair (see above).
    duplicate = (
        None
        if is_correction_pair
        else maybe_dedup(
            conn,
            workspace_id=episode_in.workspace_id,
            redacted_text=redacted.text,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            settings=auto_promote_settings,
        )
    )
    if duplicate is not None and duplicate.episode_id:
        existing_episode = get_episode(conn, duplicate.episode_id)
        existing_chunk = get_chunk(conn, duplicate.chunk_id)
        if existing_episode is not None and existing_chunk is not None:
            return EpisodeIngestResult(
                episode=existing_episode,
                chunk=existing_chunk,
                redacted_kinds=redacted.kinds_seen,
                embedded=True,
                was_duplicate=True,
                duplicate_similarity=duplicate.score,
            )

    episode, chunk = persist(conn, episode_in, redacted.text, redacted.kinds_seen)

    embedded = False
    if embedding_provider is not None and vector_store is not None:
        embedded = embed_and_upsert(chunk, redacted.text, embedding_provider, vector_store)
        if embedded:
            set_chunk_embedding_id(conn, chunk_id=chunk.id, embedding_id=chunk.id)
            upsert_vector_index_metadata(
                conn,
                workspace_id=chunk.workspace_id,
                namespace=NAMESPACE_CHUNKS,
                provider=embedding_provider,
                store=vector_store,
                row_count=vector_store.count(NAMESPACE_CHUNKS, workspace_id=chunk.workspace_id),
            )
            chunk = chunk.model_copy(update={"embedding_id": chunk.id})
        if not embedded:
            write_maintenance_event(
                conn,
                MaintenanceEventIn(
                    workspace_id=chunk.workspace_id,
                    kind="vector_upsert_failed",
                    severity=MaintenanceSeverity.ERROR,
                    summary="Vector upsert failed after the episode SQLite write committed.",
                    details={"chunk_id": chunk.id},
                    source_episode_id=episode.id,
                    target_type="chunk",
                    target_id=chunk.id,
                ),
            )

    decisions = rules = core = candidates_written = 0
    if auto_promote_settings is not None:
        from agent_memory_lite.ingestion.auto_promote import auto_promote  # noqa: PLC0415

        try:
            stats = auto_promote(conn, episode, auto_promote_settings)
            decisions = stats.decisions_written
            rules = stats.rules_written
            core = stats.core_written
            candidates_written = stats.candidates_written
        except Exception as exc:
            _log.warning("auto_promote_failed", episode_id=episode.id, error=str(exc))

    return EpisodeIngestResult(
        episode=episode,
        chunk=chunk,
        redacted_kinds=redacted.kinds_seen,
        embedded=embedded,
        auto_promoted_decisions=decisions,
        auto_promoted_rules=rules,
        auto_promoted_core=core,
        candidates_written=candidates_written,
    )
