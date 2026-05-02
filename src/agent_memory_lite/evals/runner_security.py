"""Redaction + prompt-injection + trust-gating eval helpers."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.evals.runner_retrieval import build_candidate
from agent_memory_lite.extraction.thresholds import meets_thresholds
from agent_memory_lite.extraction.trust_gate import passes_trust_gate
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.vector_store.base import VectorStore


def run_redaction(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> int:
    secret = str(case["secret"])
    text = str(case["text"]).replace("{secret}", secret)
    result = ingest_episode(
        conn,
        EpisodeIn(
            workspace_id=workspace_id,
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text=text,
            trust_level=TrustLevel.AGENT_OBSERVED,
            importance=0.5,
        ),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    leaks = 0
    if secret in result.episode.raw_text:
        leaks += 1
    if secret in result.chunk.text:
        leaks += 1
    return leaks


_INSTRUCTION_RE = re.compile(
    r"(?i)(?:ignore (?:previous|all) instructions|disregard the system prompt)"
)


def run_prompt_injection(case: dict[str, Any]) -> int:
    text = str(case.get("payload", ""))
    return 1 if _INSTRUCTION_RE.search(text) and case.get("expect_blocked", True) is False else 0


def run_trust_gating(case: dict[str, Any]) -> bool:
    candidate = build_candidate(dict(case.get("candidate", {})))
    expected = bool(case.get("expect_promotable", False))
    actual = passes_trust_gate(candidate) and meets_thresholds(candidate)
    return actual == expected
