"""Research compact-surface eval runner.

Per-kind seed handlers live in ``runner_research_seeders.py``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.evals.runner_research_seeders import HANDLERS
from agent_memory_lite.storage.reader import search
from agent_memory_lite.vector_store.base import VectorStore


def _store_label(labels: dict[str, str], entry: dict[str, Any], item_id: str) -> None:
    label = str(entry.get("label", ""))
    if label:
        labels[label] = item_id


def _seed_research_entry(
    conn: sqlite3.Connection,
    entry: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> None:
    for key, handler in HANDLERS.items():
        if key in entry:
            item_id = handler(conn, dict(entry[key]), workspace_id=workspace_id, labels=labels)
            _store_label(labels, entry, item_id)
            return


def seed_research_setup(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for entry in list(case.get("setup", [])):
        _seed_research_entry(conn, entry, workspace_id=workspace_id, labels=labels)
    return labels


def run_research_context(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> list[str]:
    del embedding_provider, vector_store
    seed_research_setup(conn, case, workspace_id)
    query_text = str(case["query"])
    hits = search(
        conn,
        workspace_id=workspace_id,
        query=query_text,
        limit=int(case.get("top_k", 10)),
    )
    brief = compose_brief(
        conn,
        workspace_id=workspace_id,
        task=query_text,
        max_tokens=int(case.get("max_tokens", 2500)),
    )
    failures: list[str] = []
    rendered = brief.body_md + "\n" + "\n".join(f"{hit.kind}: {hit.projection}" for hit in hits)
    section_names = {section.name for section in brief.sections}
    for section in case.get("expect_sections", []):
        section_name = str(section)
        if section_name not in section_names:
            failures.append(f"missing brief section {section_name!r}")
    for needle in case.get("expect_substrings", []):
        if str(needle) not in rendered:
            failures.append(f"missing substring {needle!r}")
    return failures
