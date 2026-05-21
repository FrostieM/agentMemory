"""LLM-driven lesson extraction over an episode window (v1.8).

Pure parsing lives in ``lesson_proposal.py``. This module owns the
network call to Ollama. ``extract_lessons`` is conservative: empty
window or LLM error → empty result. A miss is preferable to a
hallucinated lesson.
"""

from __future__ import annotations

import json
import logging

import httpx

from agent_memory_lite.compaction.lesson_proposal import (
    EpisodeWindowItem,
    LessonProposal,
    build_prompt,
    parse_lessons_response,
)

__all__ = [
    "EpisodeWindowItem",
    "LessonProposal",
    "build_prompt",
    "extract_lessons",
    "parse_lessons_response",
]

_log = logging.getLogger(__name__)
_DEFAULT_TIMEOUT = 30.0


def extract_lessons(
    window: list[EpisodeWindowItem],
    *,
    base_url: str,
    model: str,
    min_support_episodes: int,
    max_per_run: int,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[LessonProposal]:
    """Network-side orchestrator. Empty window → empty result, no LLM call."""
    if not window:
        return []
    prompt = build_prompt(
        window, max_per_run=max_per_run, min_support_episodes=min_support_episodes
    )
    raw = _call_ollama(base_url=base_url, model=model, prompt=prompt, timeout=timeout)
    return parse_lessons_response(
        raw,
        episode_ids_in_pool={item.id for item in window},
        min_support_episodes=min_support_episodes,
        max_per_run=max_per_run,
    )


def _call_ollama(*, base_url: str, model: str, prompt: str, timeout: float) -> str:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(
                f"{base_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0.0},
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        _log.warning("lesson_extractor_call_failed", exc_info=exc)
        return ""
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return ""
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""
