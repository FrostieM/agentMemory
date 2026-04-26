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
from typing import Any

import httpx

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.extraction.base import ExtractorUnavailableError
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import MemoryCandidateKind, TrustLevel
from agent_memory_lite.models.episodes import Episode

_log = get_logger("extraction.llm")
_DEFAULT_TIMEOUT = 30.0


def probe_ollama(settings: Settings) -> None:
    if settings.ollama_probe_skip:
        return
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{settings.llm_base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ExtractorUnavailableError(
            f"Ollama probe failed at {settings.llm_base_url}: {exc}. "
            "Install Ollama (https://ollama.com) and `ollama pull "
            f"{settings.llm_model}`, or set OLLAMA_PROBE_SKIP=true to bypass."
        ) from exc


_PROMPT = """You extract durable memory candidates from the agent's recent
event. Return a JSON array. Each item must be a JSON object with keys:
  kind: one of constraint, project_fact, decision, task_state, relationship,
        procedural_rule, correction, bug, fix
  subject: short string
  predicate: short string
  object: optional string
  evidence: short quote from the event
  confidence: float in [0, 1]
  importance: float in [0, 1]
Return [] if there is nothing durable to remember.

Event:
"""


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
            with httpx.Client(timeout=self._timeout) as client:
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
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            return []
        if not isinstance(decoded, list):
            return []

        temporal = TemporalSpan(
            observed_at=episode.created_at,
            valid_from=episode.created_at,
        )
        out: list[MemoryCandidate] = []
        for item in decoded:
            if not isinstance(item, dict):
                continue
            try:
                kind = MemoryCandidateKind(item["kind"])
            except (KeyError, ValueError):
                continue
            data: dict[str, Any] = {
                "kind": kind,
                "subject": str(item.get("subject", "")),
                "predicate": str(item.get("predicate", "")),
                "object": item.get("object"),
                "evidence": str(item.get("evidence", "")),
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
