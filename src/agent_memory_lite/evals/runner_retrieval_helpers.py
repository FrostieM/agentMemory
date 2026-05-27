"""Shared helpers for retrieval eval runners."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import EpisodeSource, MemoryCandidateKind, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.storage.reader import SearchHit, get_object
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorStore


def ingest_setup(
    conn: sqlite3.Connection,
    workspace_id: str,
    setup: list[dict[str, Any]],
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> dict[str, str]:
    label_to_chunk: dict[str, str] = {}
    for entry in setup:
        if "episode" not in entry:
            continue
        result = ingest_episode(
            conn,
            EpisodeIn(
                workspace_id=workspace_id,
                source_type=EpisodeSource.AGENT_ACTION,
                raw_text=str(entry["episode"]),
                trust_level=TrustLevel(entry.get("trust", TrustLevel.AGENT_OBSERVED.value)),
                importance=float(entry.get("importance", 0.6)),
            ),
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
        if label := str(entry.get("label", "")):
            label_to_chunk[label] = result.chunk.id
    return label_to_chunk


def build_candidate(spec: dict[str, Any]) -> MemoryCandidate:
    timestamp = iso_now()
    return MemoryCandidate(
        kind=MemoryCandidateKind(spec.get("kind", "constraint")),
        subject=str(spec.get("subject", "x")),
        predicate=str(spec.get("predicate", "is")),
        evidence=str(spec.get("evidence", "")),
        confidence=float(spec.get("confidence", 0.9)),
        importance=float(spec.get("importance", 0.85)),
        trust_level=TrustLevel(spec.get("trust_level", TrustLevel.UNKNOWN.value)),
        temporal=TemporalSpan(observed_at=timestamp, valid_from=timestamp),
        source_episode_id=spec.get("source_episode_id", "ep_synthetic"),
    )


def hit_id(hit: SearchHit) -> str:
    return str(hit.projection.get("id") or "")


def hit_sources(hit: SearchHit) -> set[str]:
    return {"fts"} if hit.kind == "chunk" else {hit.kind}


def render_surface(
    conn: sqlite3.Connection,
    workspace_id: str,
    hits: list[SearchHit],
    body_md: str,
) -> str:
    rendered: list[str] = []
    for hit in hits:
        projection = dict(hit.projection)
        if hit.kind == "chunk":
            chunk = get_object(
                conn,
                workspace_id=workspace_id,
                kind="chunk",
                object_id=hit_id(hit),
                fields=["text"],
            )
            if chunk and chunk.get("text"):
                projection["text"] = chunk["text"]
        rendered.append(f"{hit.kind}: {projection}")
    return f"{body_md}\n{'\n'.join(rendered)}"
