"""v3.2 — Ollama-augmented consolidation summaries.

The heuristic ``distill_cluster`` in ``consolidation.py`` produces lines
like "Recurring theme (5 episodes): docs, file_indexed" — bag-of-tokens
noise that downstream V1 (``experiment_proposal``) then dresses up as
"AI agent exhibits bias in decision-making process" generic hypotheses.

This module adds an optional LLM rewrite: given 3-5 representative
episode excerpts plus the signal tokens, Ollama writes a 1-line
pattern statement (≤120 chars). Failure-soft contract mirrors
``experiment_proposal_llm``: ANY failure mode (Ollama unreachable,
timeout, malformed response, env disabled) returns ``None`` and the
caller falls back to the heuristic summary. No exceptions propagate.

# Settings

* ``MEMORY_CONSOLIDATION_LLM_ENABLED`` — default ``true``. Set to false
  to restore the byte-equivalent word-frequency heuristic.
* ``MEMORY_CONSOLIDATION_LLM_TIMEOUT_SEC`` — default ``20``.

The Ollama base URL + model name are taken from the application
``Settings`` (same single config that V1 reads from).
"""

from __future__ import annotations

import os

_MAX_EXCERPTS = 5
_EXCERPT_CHARS = 220
_MAX_SUMMARY_CHARS = 160
_NO_PATTERN_SENTINEL = "NO_PATTERN"


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
    """v3.2: default ON. Ollama is mandatory local-only per project
    invariants; the body is failure-soft so an unreachable Ollama
    drops cleanly back to the heuristic. Operator sets
    ``MEMORY_CONSOLIDATION_LLM_ENABLED=false`` to opt out."""
    return _bool_env("MEMORY_CONSOLIDATION_LLM_ENABLED", True)


def timeout_sec() -> float:
    return _float_env("MEMORY_CONSOLIDATION_LLM_TIMEOUT_SEC", 20.0)


def _format_excerpts(excerpts: list[str]) -> str:
    """Render ``excerpts`` as a numbered list, capped at length + count."""
    rendered: list[str] = []
    for i, raw in enumerate(excerpts[:_MAX_EXCERPTS], start=1):
        clean = " ".join(raw.split())[:_EXCERPT_CHARS]
        rendered.append(f"{i}. {clean}")
    return "\n".join(rendered)


def _build_prompt(*, excerpts: list[str], signal_tokens: list[str], member_count: int) -> str:
    tokens_str = ", ".join(signal_tokens[:8]) if signal_tokens else "(none)"
    body = _format_excerpts(excerpts)
    return (
        "You are summarizing a cluster of related agent-memory episodes.\n"
        f"Cluster size: {member_count} episodes\n"
        f"Common tokens (raw word-frequency signal): {tokens_str}\n\n"
        f"Representative excerpts:\n{body}\n\n"
        "Write ONE concise sentence (≤120 chars) describing the recurring "
        "pattern. Focus on WHAT the episodes share (an action, a topic, a "
        "tool sequence) — not a hypothesis or recommendation. Skip generic "
        "filler like 'the agent does X'.\n\n"
        f"If you cannot find a meaningful pattern beyond the raw token list, "
        f"reply with exactly: {_NO_PATTERN_SENTINEL}\n\n"
        "Output the sentence only — no preamble, no quotes, no markdown."
    )


def _clean_response(raw: str) -> str | None:
    """Strip wrappers + length-cap. Return ``None`` on sentinel/empty."""
    text = raw.strip().strip('"').strip("'").strip()
    if not text:
        return None
    if text.upper().startswith(_NO_PATTERN_SENTINEL):
        return None
    # Take only the first line — model sometimes adds a stray newline.
    first_line = text.splitlines()[0].strip()
    if not first_line:
        return None
    return first_line[:_MAX_SUMMARY_CHARS]


def llm_distill_cluster(
    *,
    excerpts: list[str],
    signal_tokens: list[str],
    member_count: int,
    base_url: str,
    model: str,
) -> str | None:
    """Call Ollama and return a 1-line cluster summary, or ``None``.

    ``None`` means "fall back to the heuristic body". Failure modes
    that produce ``None``: feature disabled, missing base_url/model,
    httpx not installed, HTTP error, timeout, malformed JSON, empty
    response, NO_PATTERN sentinel.
    """
    if not is_llm_enabled():
        return None
    if not base_url or not model or not excerpts:
        return None
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return None
    prompt = _build_prompt(
        excerpts=excerpts, signal_tokens=signal_tokens, member_count=member_count
    )
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
    return _clean_response(raw)
