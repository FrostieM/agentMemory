"""Result model for file ingestion."""

from __future__ import annotations

from dataclasses import dataclass

from agent_memory_lite.models.files import FileRecord


@dataclass(frozen=True, slots=True)
class FileIngestResult:
    file: FileRecord
    chunks_written: int
    edges_written: int
    versions_written: int
    skipped: bool
