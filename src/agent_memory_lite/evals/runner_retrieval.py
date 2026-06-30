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
    # H7 (global-audit 2026-06-30): GATE on recall. Previously recall was computed
    # and averaged into the report but no retrieval case ever FAILED on it -- only
    # forbid_substrings could fail a case -- so a regression that dropped a relevant
    # row out of top-10 still counted as cases_passed and left run_evals green
    # (false-green). A case with expect_labels asserts those rows ARE in the top-10;
    # recall < min_recall (default 1.0, the by-design eval invariant) now fails the
    # case. Cases without expect_labels do not exercise recall.
    min_recall = float(case.get("min_recall", 1.0))
    if expected_ids and recall < min_recall:
        missing = [lbl for lbl in expected_labels if label_map.get(lbl) not in retrieved_ids]
        failures.append(
            f"recall@10 {recall:.3f} < required {min_recall:.3f}: "
            f"expected labels {missing} not in top-10"
        )
    forbidden_substrings = case.get("forbid_substrings", [])
    rendered = render_surface(conn, workspace_id, hits, "")
    for needle in forbidden_substrings:
        if str(needle) in rendered:
            failures.append(f"forbidden substring {needle!r} in compact retrieval")
    return recall, precision, failures
