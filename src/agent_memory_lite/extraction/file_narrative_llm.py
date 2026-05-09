"""2.1.3: Ollama-driven narrative for file digests.

Replaces the heuristic narrative ("Contains: 1 class, 2 functions.
Edges: 5 inbound") with a 2-3 sentence coherent paragraph when
Ollama is available and the operator has opted in via
``MEMORY_LLM_NARRATIVE_ENABLED=true``. Falls back to the heuristic
on any failure (Ollama unreachable, timeout, malformed response).

The prompt is deliberately compact — we cap the input at
``MEMORY_LLM_NARRATIVE_MAX_INPUT_CHARS`` (default 4000) to keep
inference fast and predictable on a local 7B model.
"""

from __future__ import annotations

import json

import httpx

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.logging_setup import get_logger

_log = get_logger("extraction.file_narrative_llm")

_PROMPT_TMPL = """You write 2-3 sentence summaries of source files for an
agent's memory. Reply with ONLY the summary — no markdown fences, no
preamble, no quotes. Describe what the file does and what its main
exported symbols are, in plain English. Keep it under 80 words.

File: {path}
Language: {language}

Symbols ({n_symbols}): {symbols}

Top inbound (who calls this file's symbols):
{inbound}

Top outbound (what this file uses):
{outbound}

Summary:"""


def build_prompt(
    *,
    file_path: str,
    language: str | None,
    qualified_names: list[str],
    inbound_targets: list[str],
    outbound_targets: list[str],
    max_chars: int = 4000,
) -> str:
    symbols_str = ", ".join(qualified_names[:30]) or "(none)"
    inbound_str = "\n".join(f"  - {t}" for t in inbound_targets[:10]) or "  (none)"
    outbound_str = "\n".join(f"  - {t}" for t in outbound_targets[:10]) or "  (none)"
    prompt = _PROMPT_TMPL.format(
        path=file_path,
        language=language or "?",
        n_symbols=len(qualified_names),
        symbols=symbols_str,
        inbound=inbound_str,
        outbound=outbound_str,
    )
    if len(prompt) > max_chars:
        prompt = prompt[: max_chars - 12] + "\n[truncated]"
    return prompt


def _clean_response(text: str) -> str:
    """Trim the LLM output: strip ``...``-fences, leading 'Summary:',
    drop empty surrounding lines."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Drop opening fence + optional language tag.
        first_newline = cleaned.find("\n")
        if first_newline > 0:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()
    if cleaned.lower().startswith("summary:"):
        cleaned = cleaned[len("summary:") :].lstrip()
    return cleaned.strip()


def call_llm_narrative(
    settings: Settings,
    *,
    prompt: str,
    timeout_sec: float,
) -> str | None:
    """Call Ollama's /api/chat. Returns the cleaned narrative string
    or ``None`` on any failure (network, JSON parse, empty content).
    Never raises into the caller.
    """
    base_url = settings.llm_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(
                f"{base_url}/api/chat",
                json={
                    "model": settings.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0.2},
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        _log.warning("llm_narrative_http_failed", error=str(exc))
        return None
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return None
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    cleaned = _clean_response(content)
    return cleaned or None
