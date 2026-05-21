"""v3.1 Vector 3 LLM augmentation — Ollama-based blindspot description.

When the heuristic surfaces a token that gets discussed across many
episodes but never reaches a decision, an LLM-generated short
description can answer "why does this matter?" without forcing the
operator to grep through episodes.

# Contract

* ``MEMORY_BLINDSPOT_LLM_ENABLED`` (default ``false``) — gates the
  whole module. When off, ``llm_describe_blindspot`` returns ``None``
  and the brief / dashboard show only the token + episode count.
* On any failure (Ollama unreachable, timeout, malformed response),
  returns ``None`` so the heuristic-only path remains valid.

# Settings

* ``MEMORY_BLINDSPOT_LLM_ENABLED`` — default ``false``.
* ``MEMORY_BLINDSPOT_LLM_TIMEOUT_SEC`` — default ``15``.
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
    # Live-validation-2026-05-20: flipped default to True. Same
    # rationale as Vector 1 — Ollama is mandatory per README,
    # llm_describe_blindspot is failure-soft, and the heuristic
    # surface (token + count only) is noticeably less useful than
    # the LLM-augmented "why this matters" sentence.
    return _bool_env("MEMORY_BLINDSPOT_LLM_ENABLED", True)


def timeout_sec() -> float:
    return _float_env("MEMORY_BLINDSPOT_LLM_TIMEOUT_SEC", 15.0)


def _build_prompt(token: str, episode_count: int, sample_excerpts: list[str]) -> str:
    excerpt_block = "\n".join(f"- {x}" for x in sample_excerpts[:6])
    return (
        "An AI agent's memory has surfaced a STRUCTURAL BLINDSPOT — a "
        "topic that gets discussed across many episodes but never "
        "becomes a recorded architectural decision.\n\n"
        f"Token: {token!r}\n"
        f"Distinct episodes referencing it: {episode_count}\n"
        f"Sample episode excerpts:\n{excerpt_block}\n\n"
        "In ONE short sentence (≤30 words), explain why this token "
        "deserves an explicit decision — what tension or recurring "
        "question is the agent avoiding? No preamble, no markdown, "
        "no headings."
    )


def llm_describe_blindspot(
    *,
    token: str,
    episode_count: int,
    sample_excerpts: list[str],
    base_url: str,
    model: str,
) -> str | None:
    """Call Ollama and return a one-sentence blindspot description.

    Returns ``None`` on any failure so the caller falls back to the
    plain heuristic surface (token + count only).
    """
    if not is_llm_enabled():
        return None
    if not base_url or not model:
        return None
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return None
    prompt = _build_prompt(token, episode_count, sample_excerpts)
    try:
        r = httpx.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout_sec(),
            trust_env=False,
        )
        r.raise_for_status()
        payload = r.json()
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    raw = str(payload.get("response", "")).strip()
    if not raw:
        return None
    # Single sentence — cut at first paragraph break and clamp length.
    line = raw.split("\n\n", 1)[0].strip()
    if len(line) > 280:
        line = line[:277].rstrip() + "..."
    return line
