"""Pure types + parser for reflective-compaction lesson proposals.

No Ollama / network code lives here so unit tests can run the parser
on golden inputs without a mock client.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from agent_memory_lite.extraction.llm_extractor import _strip_fences

_INSIGHT_TYPE_DEFAULT = "open_question"
VALID_INSIGHT_TYPES: frozenset[str] = frozenset(
    {
        "open_question",
        "lesson_learned",
        "anti_pattern",
        "hypothesis",
        "process_improvement",
    }
)


@dataclass(frozen=True, slots=True)
class EpisodeWindowItem:
    id: str
    summary: str


@dataclass(frozen=True, slots=True)
class LessonProposal:
    insight_type: str
    summary: str
    proposed_action: str | None
    source_episode_ids: tuple[str, ...]
    confidence: float

    def to_dict(self) -> dict[str, object]:
        return {
            "insight_type": self.insight_type,
            "summary": self.summary,
            "proposed_action": self.proposed_action,
            "source_episode_ids": list(self.source_episode_ids),
            "confidence": self.confidence,
        }


def build_prompt(
    window: list[EpisodeWindowItem],
    *,
    max_per_run: int,
    min_support_episodes: int,
) -> str:
    """Render the deterministic Ollama prompt. Pure — easy to snapshot-test."""
    lines = [f"[{item.id}] {item.summary[:240]}" for item in window]
    pool = "\n".join(lines) if lines else "(empty)"
    return (
        "You are a research-memory distiller. Read the recent agent episodes\n"
        "below. Propose at most "
        f"{max_per_run} reusable LESSONS that recur across at least "
        f"{min_support_episodes} of these episodes. Return STRICT JSON only:\n"
        '[{"insight_type": "lesson_learned", "summary": "...",\n'
        '  "proposed_action": "...", "source_episode_ids": ["ep_a","ep_b"],\n'
        '  "confidence": 0.7}]\n'
        f"Valid insight_type: {sorted(VALID_INSIGHT_TYPES)}.\n"
        "Cite real episode ids from the pool. If unsure, return [].\n"
        f"\nEPISODES:\n{pool}\n"
    )


def parse_lessons_response(
    raw: str,
    *,
    episode_ids_in_pool: set[str],
    min_support_episodes: int,
    max_per_run: int,
) -> list[LessonProposal]:
    """Validate the LLM response into LessonProposals. Drops malformed
    entries silently — a hallucination is no worse than an empty result.
    """
    if not raw.strip():
        return []
    try:
        decoded = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    out: list[LessonProposal] = []
    for entry in decoded:
        proposal = _coerce_proposal(
            entry,
            episode_ids_in_pool=episode_ids_in_pool,
            min_support_episodes=min_support_episodes,
        )
        if proposal is not None:
            out.append(proposal)
        if len(out) >= max_per_run:
            break
    return out


def _coerce_proposal(
    entry: object,
    *,
    episode_ids_in_pool: set[str],
    min_support_episodes: int,
) -> LessonProposal | None:
    if not isinstance(entry, dict):
        return None
    summary = entry.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    insight_type = entry.get("insight_type", _INSIGHT_TYPE_DEFAULT)
    if not isinstance(insight_type, str) or insight_type not in VALID_INSIGHT_TYPES:
        insight_type = _INSIGHT_TYPE_DEFAULT
    raw_ids = entry.get("source_episode_ids", [])
    if not isinstance(raw_ids, list):
        return None
    valid_ids = tuple(str(i) for i in raw_ids if str(i) in episode_ids_in_pool)
    if len(valid_ids) < min_support_episodes:
        return None
    raw_action = entry.get("proposed_action")
    proposed_action = raw_action if isinstance(raw_action, str) else None
    confidence_raw = entry.get("confidence", 0.6)
    confidence = float(confidence_raw) if isinstance(confidence_raw, int | float) else 0.6
    confidence = max(0.0, min(1.0, confidence))
    return LessonProposal(
        insight_type=insight_type,
        summary=summary.strip(),
        proposed_action=proposed_action,
        source_episode_ids=valid_ids,
        confidence=confidence,
    )
