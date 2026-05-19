"""Body builders for v3.1 experiment proposals: heuristic + LLM.

Lifted out of ``experiment_proposal.py`` so the discovery module stays
under the 150-SLOC ceiling. Two callables:

* ``heuristic_body`` — synchronous, deterministic, no I/O.
* ``try_llm_body`` — best-effort Ollama call; returns ``None`` on any
  failure so the caller falls back to the heuristic.
"""

from __future__ import annotations

# Hoisted so monkeypatch targets work cleanly in tests (and so the
# import-graph drift would show as a hard failure at import time).
from agent_memory_lite.maintenance.experiment_proposal_llm import llm_body_for_insight


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
    """Best-effort LLM call; returns ``None`` on any failure.

    Defers all imports + settings lookup to runtime so import-graph
    drift or a stale ``Settings`` schema doesn't crash the heuristic
    path. The settings ``llm_base_url`` + ``llm_model`` drive the call;
    empty values short-circuit to ``None``.
    """
    try:
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415

        settings = get_settings()
        return llm_body_for_insight(
            insight_id=insight_id,
            summary=text,
            confidence=confidence,
            base_url=str(getattr(settings, "llm_base_url", "") or ""),
            model=str(getattr(settings, "llm_model", "") or ""),
        )
    except Exception:  # pragma: no cover - defensive
        return None
