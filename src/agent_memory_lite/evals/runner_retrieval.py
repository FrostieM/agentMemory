"""Retrieval + retrieval-context-quality eval helpers.

Split out of ``runner.py`` so each eval kind lives in its own module
under the SLOC ceiling.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.evals.metrics import precision_at_k, recall_at_k
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import EpisodeSource, MemoryCandidateKind, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context
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
        text = str(entry["episode"])
        label = str(entry.get("label", ""))
        result = ingest_episode(
            conn,
            EpisodeIn(
                workspace_id=workspace_id,
                source_type=EpisodeSource.AGENT_ACTION,
                raw_text=text,
                trust_level=TrustLevel(entry.get("trust", TrustLevel.AGENT_OBSERVED.value)),
                importance=float(entry.get("importance", 0.6)),
            ),
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
        if label:
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


def run_retrieval(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> tuple[float, float, list[str]]:
    label_map = ingest_setup(
        conn,
        workspace_id,
        list(case.get("setup", [])),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    query = RetrievalQuery(workspace_id=workspace_id, query=str(case["query"]))
    built = build_context(
        conn, query, embedding_provider=embedding_provider, vector_store=vector_store
    )
    retrieved_ids = [hit.id for hit in built.hits]
    expected_labels = [str(label) for label in case.get("expect_labels", [])]
    expected_ids = [label_map[label] for label in expected_labels if label in label_map]
    recall = recall_at_k(retrieved_ids, expected_ids)
    precision = precision_at_k(retrieved_ids, expected_ids)
    failures: list[str] = []
    forbidden_substrings = case.get("forbid_substrings", [])
    rendered = built.text
    for needle in forbidden_substrings:
        if str(needle) in rendered:
            failures.append(f"forbidden substring {needle!r} in context")
    return recall, precision, failures


def run_retrieval_context_quality(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> tuple[float, float, list[str]]:
    label_map = ingest_setup(
        conn,
        workspace_id,
        list(case.get("setup", [])),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    top_k = max(1, int(case.get("top_k", 10)))
    query = RetrievalQuery(
        workspace_id=workspace_id,
        query=str(case["query"]),
        max_tokens=int(case.get("max_tokens", 2500)),
    )
    built = build_context(
        conn, query, embedding_provider=embedding_provider, vector_store=vector_store
    )
    top_hits = built.hits[:top_k]
    retrieved_ids = [hit.id for hit in top_hits]
    expected_labels = [str(label) for label in case.get("expect_labels", [])]
    expected_ids = [label_map[label] for label in expected_labels if label in label_map]
    recall = recall_at_k(retrieved_ids, expected_ids, k=top_k)
    precision = precision_at_k(retrieved_ids, expected_ids, k=top_k)
    failures: list[str] = []
    missing_ids = [chunk_id for chunk_id in expected_ids if chunk_id not in retrieved_ids]
    if missing_ids:
        failures.append(f"missing expected ids in top {top_k}: {missing_ids}")
    expected_sources = [str(source) for source in case.get("expect_sources", [])]
    expected_source_ids = set(expected_ids) if expected_ids else set(retrieved_ids)
    source_map = {hit.id: set(hit.sources) for hit in top_hits}
    for source in expected_sources:
        if not any(source in source_map.get(chunk_id, set()) for chunk_id in expected_source_ids):
            failures.append(f"missing expected retrieval source {source!r}")
    for section in case.get("expect_sections", []):
        if f"<{section}" not in built.text:
            failures.append(f"missing section <{section}>")
    for needle in case.get("expect_substrings", []):
        if str(needle) not in built.text:
            failures.append(f"missing substring {needle!r}")
    return recall, precision, failures
