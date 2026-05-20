"""Body builders for v3.1 experiment proposals: heuristic + LLM.

Lifted out of ``experiment_proposal.py`` so the discovery module stays
under the 150-SLOC ceiling. Two callables:

* ``heuristic_body`` — synchronous, deterministic, no I/O.
* ``try_llm_body`` — best-effort Ollama call; returns ``None`` on any
  failure so the caller falls back to the heuristic.

v3.3 noise filter: ``looks_like_generic_llm_noise`` rejects LLM bodies
that follow the "AI agent exhibits..." / "The agent demonstrates..."
template — those are word-frequency consolidation noise dressed up by
the model, not real hypotheses. When the filter trips we return
``None`` so the caller falls back to the heuristic body (which at
least quotes the source insight verbatim).
"""

from __future__ import annotations

import re

# Hoisted so monkeypatch targets work cleanly in tests (and so the
# import-graph drift would show as a hard failure at import time).
from agent_memory_lite.maintenance.experiment_proposal_llm import llm_body_for_insight

# Generic-noise pattern observed on copyBot live-2026-05-20:
# bodies that open with the actor ("AI agent" / "The agent") AND
# contain a generic verb (exhibits / demonstrates / improves / ...)
# in the same opening clause. Real domain hypotheses lead with the
# substrate (Quarter-Kelly, boot-gate filter, ...), not the actor.
_NOISE_ACTOR = re.compile(
    r"^(hypothesis\s*[:.-]?\s*)?(the\s+)?(ai\s+agent|agent)\b",
    re.IGNORECASE,
)
_NOISE_VERB = re.compile(
    r"\b(exhibits|demonstrates|shows|displays|performs|improves|tends|requires)\b",
    re.IGNORECASE,
)


def looks_like_generic_llm_noise(hypothesis_text: str) -> bool:
    """Return True when the hypothesis matches the generic
    "AI agent exhibits / improves / demonstrates ..." template.

    Trips iff BOTH conditions hold in the first 120 chars:

    1. Opens with the actor ("AI agent" / "The agent" / "The AI agent",
       optionally preceded by 'Hypothesis:').
    2. Contains one of the generic verbs (exhibits / demonstrates /
       shows / displays / performs / improves / tends / requires).

    The two-signal check keeps real hypotheses safe — e.g. "The AI
    agent successfully replays calibrator data for 50 days" passes
    (actor matches but no generic verb), while "The AI agent's
    performance improves incrementally" trips (actor + 'improves').
    """
    if not hypothesis_text or not hypothesis_text.strip():
        return True
    head = hypothesis_text.strip()[:120]
    return _NOISE_ACTOR.match(head) is not None and _NOISE_VERB.search(head) is not None


def heuristic_body(insight_id: str, text: str, confidence: float) -> tuple[str, str]:
    """Deterministic placeholder body — used when LLM augmentation is
    off OR Ollama is unreachable."""
    proposal_text = f"Hypothesis (from insight {insight_id}, conf={confidence:.2f}): {text}"
    validation_criterion = (
        f"Validate by surfacing 2+ episodes or 1 experiment that "
        f"confirms or contradicts: {text[:120]}"
    )
    return proposal_text, validation_criterion


def try_llm_body(insight_id: str, text: str, confidence: float) -> tuple[str, str] | None:
    """Best-effort LLM call; returns ``None`` on any failure OR when
    the generated body matches the generic-noise pattern.

    Defers all imports + settings lookup to runtime so import-graph
    drift or a stale ``Settings`` schema doesn't crash the heuristic
    path. The settings ``llm_base_url`` + ``llm_model`` drive the call;
    empty values short-circuit to ``None``.
    """
    try:
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415

        settings = get_settings()
        body = llm_body_for_insight(
            insight_id=insight_id,
            summary=text,
            confidence=confidence,
            base_url=str(getattr(settings, "llm_base_url", "") or ""),
            model=str(getattr(settings, "llm_model", "") or ""),
        )
    except Exception:  # pragma: no cover - defensive
        return None
    if body is None:
        return None
    hypothesis, _validation = body
    if looks_like_generic_llm_noise(hypothesis):
        # v3.3: drop the polished noise → caller falls back to
        # heuristic body that at least quotes the insight verbatim.
        return None
    return body
