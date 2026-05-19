"""v3.1 Vector 1 LLM augmentation — Ollama-based proposal generation.

Per ``docs/V3_1_BREAKTHROUGH_ROADMAP.md`` Vector 1 next milestone:
upgrade the heuristic body to LLM-generated hypothesis + falsifiable
predictions + validation criteria while preserving the same
``ExperimentProposal`` surface.

# Contract

* When ``MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED=true`` (default false
  for backward compat) and Ollama is reachable, ``llm_body_for_insight``
  returns ``(proposal_text, validation_criterion)`` synthesized by the
  configured Ollama model.
* On any failure (Ollama unreachable, timeout, malformed response,
  http error, env disabled) it returns ``None`` so the caller falls
  back to the heuristic. No exceptions propagate.

# Settings

* ``MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED`` — default ``false``.
* ``MEMORY_EXPERIMENT_PROPOSAL_LLM_TIMEOUT_SEC`` — default ``20``.
* The Ollama base URL + model name are read from the application
  ``Settings`` so a single env config drives every LLM caller.
"""

from __future__ import annotations

import os


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def is_llm_enabled() -> bool:
    # Live-validation-2026-05-20: flipped default to True after the
    # heuristic body proved noisy on real workspaces. Ollama is part
    # of the project's mandatory local-only stack (README) and the
    # llm_body_for_insight contract is failure-soft — any failure
    # returns None and the caller falls back to heuristic. Operator
    # can set MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED=false to opt out.
    return _bool_env("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", True)


def timeout_sec() -> float:
    return _float_env("MEMORY_EXPERIMENT_PROPOSAL_LLM_TIMEOUT_SEC", 20.0)


def _build_prompt(insight_id: str, summary: str, confidence: float) -> str:
    return (
        "You are turning an uncertain memory insight into a formal "
        "experiment proposal for an AI agent.\n"
        f"Insight id: {insight_id}\n"
        f"Current confidence: {confidence:.2f} (uncertain — needs testing)\n"
        f"Insight body: {summary}\n\n"
        "Produce TWO short paragraphs separated by a single blank line:\n"
        "1. HYPOTHESIS — a falsifiable claim restating the insight as a "
        "testable statement (≤40 words).\n"
        "2. VALIDATION — one concrete validation criterion that would "
        "confirm OR contradict the hypothesis (≤40 words).\n\n"
        "No lists, no preamble, no markdown headers. Just the two paragraphs."
    )


def _split_two_paragraphs(raw: str) -> tuple[str, str] | None:
    """Parse the Ollama response into (hypothesis, validation).

    The model is instructed to return two blank-line-separated
    paragraphs. We tolerate extra whitespace and surface ``None`` when
    the response doesn't have at least two non-empty chunks so the
    caller falls back to the heuristic.
    """
    chunks = [p.strip() for p in raw.split("\n\n") if p.strip()]
    if len(chunks) < 2:
        return None
    hypothesis = chunks[0]
    validation = chunks[1]
    if not hypothesis or not validation:
        return None
    return hypothesis, validation


def llm_body_for_insight(
    *,
    insight_id: str,
    summary: str,
    confidence: float,
    base_url: str,
    model: str,
) -> tuple[str, str] | None:
    """Call Ollama and return (proposal_text, validation_criterion).

    Returns ``None`` on ANY failure mode — the caller treats ``None``
    as "fall back to the heuristic body". This contract keeps the
    rest of Vector 1 byte-equivalent when Ollama is offline.
    """
    if not is_llm_enabled():
        return None
    if not base_url or not model:
        return None
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return None
    prompt = _build_prompt(insight_id, summary, confidence)
    try:
        r = httpx.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout_sec(),
        )
        r.raise_for_status()
        payload = r.json()
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    raw = str(payload.get("response", "")).strip()
    if not raw:
        return None
    return _split_two_paragraphs(raw)
