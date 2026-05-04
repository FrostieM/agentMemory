"""Run extractors over a freshly-ingested episode and persist candidates.

The pipeline runs the heuristic extractor (always) and, if configured, the
Ollama LLM extractor. Each candidate goes through `meets_thresholds` and
`passes_trust_gate`. Survivors land in `memory_candidates` for explicit review
instead of mutating active decisions, procedural rules, or core memory.

Failures here never raise into the caller. Extraction is best-effort: we log and
return `AutoPromoteStats` so the pipeline can include it in the result.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.extraction.base import Extractor
from agent_memory_lite.extraction.correction_extractor import CorrectionExtractor
from agent_memory_lite.extraction.heuristic_extractor import HeuristicExtractor
from agent_memory_lite.extraction.llm_extractor import OllamaExtractor
from agent_memory_lite.extraction.thresholds import meets_thresholds
from agent_memory_lite.extraction.trust_gate import passes_trust_gate
from agent_memory_lite.ingestion.candidate_writer import write_memory_candidate
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.candidates import MemoryCandidate
from agent_memory_lite.models.episodes import Episode

_log = get_logger("ingestion.auto_promote")


@dataclass(frozen=True, slots=True)
class AutoPromoteStats:
    candidates_seen: int
    candidates_kept: int
    decisions_written: int
    rules_written: int
    core_written: int
    candidates_written: int
    skipped_kinds: list[str]


def _build_extractors(
    settings: Settings, conn: sqlite3.Connection | None = None
) -> list[Extractor]:
    extractors: list[Extractor] = [HeuristicExtractor()]
    # v1.10 correction-aware extractor needs DB access to resolve the
    # paired claim text. Only registered when the loop is enabled AND
    # the caller passed a connection (auto_promote always does; tests
    # that build extractors standalone may opt out).
    if settings.correction_detect_enabled and conn is not None:
        extractors.append(
            CorrectionExtractor(
                conn,
                min_user_len=settings.correction_min_user_len,
                min_agent_len=settings.correction_min_agent_len,
                min_confidence=settings.correction_min_confidence,
                max_per_day=settings.correction_max_per_day,
            )
        )
    if settings.llm_backend == "ollama" and not settings.ollama_probe_skip:
        extractors.append(OllamaExtractor(settings.llm_base_url, settings.llm_model))
    return extractors


def _filter(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    return [
        candidate
        for candidate in candidates
        if passes_trust_gate(candidate) and meets_thresholds(candidate)
    ]


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())[:80]


def auto_promote(
    conn: sqlite3.Connection,
    episode: Episode,
    settings: Settings,
) -> AutoPromoteStats:
    candidates: list[MemoryCandidate] = []
    for extractor in _build_extractors(settings, conn):
        try:
            candidates.extend(extractor.extract(episode))
        except Exception as exc:
            _log.warning("extractor_failed", extractor=extractor.name, error=str(exc))

    survivors = _filter(candidates)
    candidates_written = 0
    skipped: list[str] = []
    seen: set[tuple[str, str]] = set()
    for candidate in survivors:
        dedup_key = (candidate.kind.value, _normalize(candidate.subject))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        try:
            write_memory_candidate(conn, workspace_id=episode.workspace_id, candidate=candidate)
            candidates_written += 1
        except Exception as exc:
            _log.warning(
                "memory_candidate_write_failed",
                kind=candidate.kind.value,
                subject=candidate.subject[:120],
                error=str(exc),
            )
            skipped.append(candidate.kind.value)

    return AutoPromoteStats(
        candidates_seen=len(candidates),
        candidates_kept=len(survivors),
        decisions_written=0,
        rules_written=0,
        core_written=0,
        candidates_written=candidates_written,
        skipped_kinds=skipped,
    )
