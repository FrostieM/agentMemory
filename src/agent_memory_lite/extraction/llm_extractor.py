"""Ollama-backed LLM extractor.

Two surfaces:

- `probe_ollama(settings)` — startup-time reachability check. Raises
  `ExtractorUnavailableError` if Ollama is not reachable at the configured base
  URL and the operator has not opted out via `OLLAMA_PROBE_SKIP=true`.
- `OllamaExtractor` — runs a structured-prompt extraction pass when called.
  Returns `[]` on failure; never raises into the ingest pipeline.

The structured prompt is deliberately simple: ask the LLM to return a JSON
array of MemoryCandidate-shaped objects. We then validate each entry through
pydantic so malformed output is dropped silently.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.extraction.base import ExtractorUnavailableError
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import EpisodeSource, MemoryCandidateKind, TrustLevel
from agent_memory_lite.models.episodes import Episode
from agent_memory_lite.redaction import redact


def _scrub(value: str | None) -> str | None:
    """Round-2 audit: re-redact LLM-extractor output. The episode
    raw_text is redacted upstream, but the Ollama model can echo a
    secret from the prompt into ``subject`` / ``evidence`` / ``object``;
    those fields flow into ``memory_candidates`` rows + the audit log
    without ever passing the redactor again. Scrub on the way out.
    ``redact`` rejects None, so guard the optional field."""
    if not value:
        return value
    return redact(value).text


_log = get_logger("extraction.llm")
_DEFAULT_TIMEOUT = 30.0


def probe_ollama(settings: Settings) -> None:
    if settings.ollama_probe_skip:
        return
    try:
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            response = client.get(f"{settings.llm_base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ExtractorUnavailableError(
            f"Ollama probe failed at {settings.llm_base_url}: {exc}. "
            "Install Ollama (https://ollama.com) and `ollama pull "
            f"{settings.llm_model}`, or set OLLAMA_PROBE_SKIP=true to bypass."
        ) from exc


_PROMPT = """You extract durable memory candidates from the agent's recent
event. Reply with ONLY a JSON array — no markdown fences, no prose, no
explanation, no leading or trailing text. Each item must be a JSON object
with keys:
  kind: one of constraint, project_fact, decision, task_state, relationship,
        procedural_rule, correction, bug, fix
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


class OllamaExtractor:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def _call_chat(self, prompt: str) -> str:
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                response = client.post(
                    f"{self._base_url}/api/chat",
                    json={
                        "model": self._model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "options": {"temperature": 0.0},
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            _log.warning("ollama_chat_failed", error=str(exc))
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

    def _parse(self, content: str, episode: Episode) -> list[MemoryCandidate]:
        if not content.strip():
            return []
        cleaned = _strip_fences(content)
        try:
            decoded = json.loads(cleaned)
        except json.JSONDecodeError:
            return []
        if not isinstance(decoded, list):
            return []

        temporal = TemporalSpan(
            observed_at=episode.created_at,
            valid_from=episode.created_at,
        )
        # 1.2.4: CORRECTION-kind candidates are reserved for the
        # v1.10 (claim, correction) pair flow which the heuristic
        # CorrectionExtractor handles. The LLM extractor was observed
        # in copyBot 2026-05-05 to emit ``kind=correction`` from
        # ``agent_action`` episodes that contained audit-findings
        # phrasing ("HIGH issue X — fixed"); those are NOT user
        # corrections and pollute the operator review queue. Restrict
        # CORRECTION-from-LLM to episodes where source_type is
        # USER_MESSAGE so the kind keeps its v1.10 semantics.
        episode_is_user_message = episode.source_type == EpisodeSource.USER_MESSAGE
        out: list[MemoryCandidate] = []
        for item in decoded:
            if not isinstance(item, dict):
                continue
            try:
                kind = MemoryCandidateKind(item["kind"])
            except (KeyError, ValueError):
                continue
            if kind == MemoryCandidateKind.CORRECTION and not episode_is_user_message:
                continue
            raw_object = item.get("object")
            data: dict[str, Any] = {
                "kind": kind,
                # Round-2 audit: scrub the model-returned free-text
                # fields — the LLM can echo a secret the redactor on
                # raw_text already removed.
                "subject": _scrub(str(item.get("subject", ""))),
                "predicate": _scrub(str(item.get("predicate", ""))),
                "object": _scrub(str(raw_object)) if raw_object is not None else None,
                "evidence": _scrub(str(item.get("evidence", ""))),
                "confidence": float(item.get("confidence", 0.0)),
                "importance": float(item.get("importance", 0.0)),
                "trust_level": TrustLevel.AGENT_INFERRED,
                "temporal": temporal,
                "source_episode_id": episode.id,
                "write_targets": [],
            }
            try:
                out.append(MemoryCandidate(**data))
            except Exception:
                continue
        return out

    def extract(self, episode: Episode) -> list[MemoryCandidate]:
        if not (episode.raw_text or "").strip():
            return []
        content = self._call_chat(_PROMPT + episode.raw_text)
        return self._parse(content, episode)
