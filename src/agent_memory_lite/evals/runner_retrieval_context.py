"""Retrieval context-quality eval runner."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.evals.metrics import precision_at_k, recall_at_k
from agent_memory_lite.evals.runner_retrieval_helpers import (
    hit_id,
    hit_sources,
    ingest_setup,
    render_surface,
)
from agent_memory_lite.storage.reader import SearchHit, search
from agent_memory_lite.vector_store.base import VectorStore


def run_retrieval_context_quality(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> tuple[float, float, list[str]]:
    del embedding_provider, vector_store
    label_map = ingest_setup(
        conn,
        workspace_id,
        list(case.get("setup", [])),
        embedding_provider=None,
        vector_store=None,
    )
    top_k = max(1, int(case.get("top_k", 10)))
    query_text = str(case["query"])
    hits = search(
        conn,
        workspace_id=workspace_id,
        query=query_text,
        limit=max(top_k, 10),
    )
    brief = compose_brief(
        conn,
        workspace_id=workspace_id,
        task=query_text,
        max_tokens=int(case.get("max_tokens", 2500)),
    )
    top_hits = hits[:top_k]
    retrieved_ids = [hit_id(hit) for hit in top_hits if hit_id(hit)]
    expected_labels = [str(label) for label in case.get("expect_labels", [])]
    expected_ids = [label_map[label] for label in expected_labels if label in label_map]
    recall = recall_at_k(retrieved_ids, expected_ids, k=top_k)
    precision = precision_at_k(retrieved_ids, expected_ids, k=top_k)
    failures = _context_quality_failures(
        conn,
        case,
        workspace_id,
        hits,
        top_hits,
        expected_ids,
        retrieved_ids,
        brief.body_md,
        {section.name for section in brief.sections},
        top_k,
    )
    return recall, precision, failures


def _context_quality_failures(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
    hits: list[SearchHit],
    top_hits: list[SearchHit],
    expected_ids: list[str],
    retrieved_ids: list[str],
    body_md: str,
    section_names: set[str],
    top_k: int,
) -> list[str]:
    failures = _missing_expected_ids(expected_ids, retrieved_ids, top_k)
    failures.extend(_missing_expected_sources(case, top_hits, expected_ids, retrieved_ids))
    failures.extend(_missing_expected_sections(case, section_names))
    rendered = render_surface(conn, workspace_id, hits, body_md)
    for needle in case.get("expect_substrings", []):
        if str(needle) not in rendered:
            failures.append(f"missing substring {needle!r}")
    return failures


def _missing_expected_ids(
    expected_ids: list[str],
    retrieved_ids: list[str],
    top_k: int,
) -> list[str]:
    missing_ids = [chunk_id for chunk_id in expected_ids if chunk_id not in retrieved_ids]
    return [f"missing expected ids in top {top_k}: {missing_ids}"] if missing_ids else []


def _missing_expected_sources(
    case: dict[str, Any],
    top_hits: list[SearchHit],
    expected_ids: list[str],
    retrieved_ids: list[str],
) -> list[str]:
    expected_sources = [str(source) for source in case.get("expect_sources", [])]
    expected_source_ids = set(expected_ids) if expected_ids else set(retrieved_ids)
    source_map = {hit_id(hit): hit_sources(hit) for hit in top_hits if hit_id(hit)}
    failures: list[str] = []
    for source in expected_sources:
        if not any(source in source_map.get(chunk_id, set()) for chunk_id in expected_source_ids):
            failures.append(f"missing expected retrieval source {source!r}")
    return failures


def _missing_expected_sections(
    case: dict[str, Any],
    section_names: set[str],
) -> list[str]:
    failures: list[str] = []
    for section in case.get("expect_sections", []):
        if str(section) not in section_names:
            failures.append(f"missing brief section {section!r}")
    return failures
