"""Run extractors over a freshly-ingested episode and promote survivors.

The pipeline runs the heuristic extractor (always) and, if configured, the
Ollama LLM extractor. Each candidate goes through `meets_thresholds` +
`passes_trust_gate`. Survivors land in the appropriate write surface:

- `MemoryCandidateKind.DECISION`         -> decisions table
- `MemoryCandidateKind.PROCEDURAL_RULE`  -> procedural_rules table
- `MemoryCandidateKind.CONSTRAINT`       -> core_memory (key=subject)

Other kinds (PROJECT_FACT, RELATIONSHIP, BUG, FIX, CORRECTION, TASK_STATE)
are recorded in the audit log but not auto-promoted in v1 — they need
entity resolution which we keep behind the explicit graph API.

Failures here never raise into the caller. Extraction is best-effort: we
log and return `AutoPromoteStats` so the pipeline can include it in the
result.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.extraction.base import Extractor
from agent_memory_lite.extraction.heuristic_extractor import HeuristicExtractor
from agent_memory_lite.extraction.llm_extractor import OllamaExtractor
from agent_memory_lite.extraction.thresholds import meets_thresholds
from agent_memory_lite.extraction.trust_gate import passes_trust_gate
from agent_memory_lite.ingestion.core_memory_writer import write_core_memory
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.procedural_writer import write_procedural_rule
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.candidates import MemoryCandidate
from agent_memory_lite.models.core_memory import CoreMemoryIn
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import MemoryCandidateKind
from agent_memory_lite.models.episodes import Episode
from agent_memory_lite.models.procedural import ProceduralRuleIn

_log = get_logger("ingestion.auto_promote")


@dataclass(frozen=True, slots=True)
class AutoPromoteStats:
    candidates_seen: int
    candidates_kept: int
    decisions_written: int
    rules_written: int
    core_written: int
    skipped_kinds: list[str]


def _build_extractors(settings: Settings) -> list[Extractor]:
    extractors: list[Extractor] = [HeuristicExtractor()]
    if settings.llm_backend == "ollama" and not settings.ollama_probe_skip:
        extractors.append(OllamaExtractor(settings.llm_base_url, settings.llm_model))
    return extractors


def _filter(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    return [c for c in candidates if passes_trust_gate(c) and meets_thresholds(c)]


def _promote_decision(conn: sqlite3.Connection, candidate: MemoryCandidate) -> bool:
    title = candidate.subject[:80] or "Auto-extracted decision"
    try:
        write_decision(
            conn,
            DecisionIn(
                workspace_id="default",
                title=title,
                decision_text=candidate.evidence or candidate.subject,
                rationale=None,
                source_episode_id=candidate.source_episode_id,
                confidence=candidate.confidence,
                importance=candidate.importance,
            ),
        )
    except Exception as exc:
        _log.warning("auto_promote_decision_failed", error=str(exc))
        return False
    return True


def _promote_rule(conn: sqlite3.Connection, candidate: MemoryCandidate) -> bool:
    try:
        write_procedural_rule(
            conn,
            ProceduralRuleIn(
                workspace_id="default",
                rule_text=candidate.evidence or candidate.subject,
                source_episode_id=candidate.source_episode_id,
                confidence=candidate.confidence,
                importance=candidate.importance,
            ),
        )
    except Exception as exc:
        _log.warning("auto_promote_rule_failed", error=str(exc))
        return False
    return True


def _promote_core(conn: sqlite3.Connection, candidate: MemoryCandidate) -> bool:
    try:
        write_core_memory(
            conn,
            CoreMemoryIn(
                workspace_id="default",
                key=candidate.subject.strip().lower()[:80] or "auto.constraint",
                value=candidate.evidence or candidate.subject,
                source_episode_id=candidate.source_episode_id,
                confidence=candidate.confidence,
                importance=candidate.importance,
            ),
        )
    except Exception as exc:
        _log.warning("auto_promote_core_failed", error=str(exc))
        return False
    return True


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())[:80]


def auto_promote(
    conn: sqlite3.Connection,
    episode: Episode,
    settings: Settings,
) -> AutoPromoteStats:
    candidates: list[MemoryCandidate] = []
    for extractor in _build_extractors(settings):
        try:
            candidates.extend(extractor.extract(episode))
        except Exception as exc:
            _log.warning("extractor_failed", extractor=extractor.name, error=str(exc))

    survivors = _filter(candidates)
    decisions = rules = core = 0
    skipped: list[str] = []
    seen: set[tuple[str, str]] = set()
    for candidate in survivors:
        dedup_key = (candidate.kind.value, _normalize(candidate.subject))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        if candidate.kind == MemoryCandidateKind.DECISION and _promote_decision(conn, candidate):
            decisions += 1
        elif candidate.kind == MemoryCandidateKind.PROCEDURAL_RULE and _promote_rule(
            conn, candidate
        ):
            rules += 1
        elif candidate.kind == MemoryCandidateKind.CONSTRAINT and _promote_core(conn, candidate):
            core += 1
        else:
            skipped.append(candidate.kind.value)

    return AutoPromoteStats(
        candidates_seen=len(candidates),
        candidates_kept=len(survivors),
        decisions_written=decisions,
        rules_written=rules,
        core_written=core,
        skipped_kinds=skipped,
    )
