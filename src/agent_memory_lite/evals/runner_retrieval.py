"""Retrieval + compact-surface quality eval helpers.

Split out of ``runner.py`` so each eval kind lives in its own module
under the SLOC ceiling.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.evals.metrics import precision_at_k, recall_at_k
from agent_memory_lite.evals.runner_retrieval_context import run_retrieval_context_quality
from agent_memory_lite.evals.runner_retrieval_helpers import (
    build_candidate,
    hit_id,
    ingest_setup,
    render_surface,
)
from agent_memory_lite.storage.reader import search
from agent_memory_lite.vector_store.base import VectorStore

__all__ = [
    "build_candidate",
    "ingest_setup",
    "run_retrieval",
    "run_retrieval_context_quality",
]


def run_retrieval(
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
    hits = search(
        conn,
        workspace_id=workspace_id,
        query=str(case["query"]),
        limit=10,
    )
    retrieved_ids = [hit_id(hit) for hit in hits if hit_id(hit)]
    expected_labels = [str(label) for label in case.get("expect_labels", [])]
    expected_ids = [label_map[label] for label in expected_labels if label in label_map]
    recall = recall_at_k(retrieved_ids, expected_ids)
    precision = precision_at_k(retrieved_ids, expected_ids)
    failures: list[str] = []
    forbidden_substrings = case.get("forbid_substrings", [])
    rendered = render_surface(conn, workspace_id, hits, "")
    for needle in forbidden_substrings:
        if str(needle) in rendered:
            failures.append(f"forbidden substring {needle!r} in compact retrieval")
    return recall, precision, failures
