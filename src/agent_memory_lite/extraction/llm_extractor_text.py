"""Text-processing helpers for the Ollama LLM extractor.

Three concerns live here, all pure / I/O-free:

- ``_PROMPT`` — the structured extraction prompt the model is given.
- ``_strip_fences`` — normalise the model's reply down to the JSON array,
  tolerating markdown fences or surrounding prose.
- ``_scrub`` — round-2 redaction of model-returned free-text fields.

These are split out of ``llm_extractor`` to keep that module small; they are
re-exported from it so the original import path is unchanged.
"""

from __future__ import annotations

import re

from agent_memory_lite.redaction import redact


def _scrub(value: str | None) -> str | None:
    """Round-2 audit: re-redact LLM-extractor output. The episode
    raw_text is redacted upstream, but the Ollama model can echo a
    secret from the prompt into ``subject`` / ``evidence`` / ``object``;
    those fields flow into ``candidates`` rows + the audit log
    without ever passing the redactor again. Scrub on the way out.
    ``redact`` rejects None, so guard the optional field."""
    if not value:
        return value
    return redact(value).text


_PROMPT = """You extract durable memory candidates from the agent's recent
event. Reply with ONLY a JSON array — no markdown fences, no prose, no
explanation, no leading or trailing text. Each item must be a JSON object
with keys:
  kind: one of constraint, decision, relationship, rule, correction, bug, fix
  subject: short string
  predicate: short string
  object: optional string
  evidence: short quote from the event
  confidence: float in [0, 1]
  importance: float in [0, 1]
If there is nothing durable to remember, reply with exactly: []

Event:
"""

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _strip_fences(content: str) -> str:
    match = _FENCE_RE.search(content)
    if match:
        return match.group(1).strip()
    # Tolerate prose around the JSON: take from first '[' to last ']'.
    start = content.find("[")
    end = content.rfind("]")
    if 0 <= start < end:
        return content[start : end + 1]
    return content.strip()
