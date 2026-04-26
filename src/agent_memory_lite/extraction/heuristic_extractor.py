"""Regex-based extractor.

Looks for explicit cues in episode text:

- "Decision: ..." / "Решение: ..." → DECISION candidate.
- "Rule: ..." / "Правило: ..." → PROCEDURAL_RULE candidate (only when the
  episode is user-asserted; the trust gate also enforces this).

The extractor never produces high-confidence claims on its own — it surfaces
cues so the writer + thresholds can decide what to persist.
"""

from __future__ import annotations

import re

from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import MemoryCandidateKind
from agent_memory_lite.models.episodes import Episode

_DECISION_RE = re.compile(r"(?im)^\s*(?:decision|решение)\s*[:\-]\s*(.+?)\s*$")
_RULE_RE = re.compile(r"(?im)^\s*(?:rule|правило)\s*[:\-]\s*(.+?)\s*$")


class HeuristicExtractor:
    name = "heuristic"

    def extract(self, episode: Episode) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        text = episode.raw_text or ""
        if not text:
            return candidates

        temporal = TemporalSpan(
            observed_at=episode.created_at,
            valid_from=episode.created_at,
        )

        for match in _DECISION_RE.finditer(text):
            body = match.group(1).strip()
            if not body:
                continue
            candidates.append(
                MemoryCandidate(
                    kind=MemoryCandidateKind.DECISION,
                    subject=body,
                    predicate="declared",
                    evidence=match.group(0),
                    confidence=0.85,
                    importance=0.80,
                    trust_level=episode.trust_level,
                    temporal=temporal,
                    source_episode_id=episode.id,
                    write_targets=["decision", "audit"],
                )
            )

        for match in _RULE_RE.finditer(text):
            body = match.group(1).strip()
            if not body:
                continue
            candidates.append(
                MemoryCandidate(
                    kind=MemoryCandidateKind.PROCEDURAL_RULE,
                    subject=body,
                    predicate="declared",
                    evidence=match.group(0),
                    confidence=0.85,
                    importance=0.75,
                    trust_level=episode.trust_level,
                    temporal=temporal,
                    source_episode_id=episode.id,
                    write_targets=["procedural", "audit"],
                )
            )

        return candidates
