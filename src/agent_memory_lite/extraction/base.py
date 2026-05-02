"""Extractor contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_memory_lite.models.candidates import MemoryCandidate
from agent_memory_lite.models.episodes import Episode


class ExtractorUnavailableError(RuntimeError):
    """Raised when an extractor backend is configured but unreachable."""


@runtime_checkable
class Extractor(Protocol):
    name: str

    def extract(self, episode: Episode) -> list[MemoryCandidate]: ...
