"""Walk a workspace, yield ingestable files.

Applies the exclude rules and the size cap. Returns an iterator of
`ScannedFile` records the file pipeline can ingest one at a time.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from agent_memory_lite.ingestion.exclude_rules import (
    DEFAULT_MAX_FILE_BYTES,
    ExcludePattern,
    collect_patterns,
    is_excluded,
)


@dataclass(frozen=True, slots=True)
class ScannedFile:
    absolute_path: Path
    relative_path: str
    size_bytes: int


def _detect_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            sample = fh.read(2048)
    except OSError:
        return True
    return b"\x00" in sample


def scan_workspace(
    root: str | Path,
    *,
    patterns: list[ExcludePattern] | None = None,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> Iterator[ScannedFile]:
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        return
    patterns = patterns if patterns is not None else collect_patterns(root_path)

    for entry in root_path.rglob("*"):
        rel_parts = entry.relative_to(root_path).parts
        if not rel_parts:
            continue
        rel_path = "/".join(rel_parts)
        if entry.is_dir():
            if is_excluded(rel_path + "/", is_dir=True, patterns=patterns):
                # Skip excluded directories entirely; rglob keeps walking but
                # we drop their children below as well.
                continue
            continue
        if is_excluded(rel_path, is_dir=False, patterns=patterns):
            continue
        if any(
            is_excluded("/".join(rel_parts[: i + 1]) + "/", is_dir=True, patterns=patterns)
            for i in range(len(rel_parts) - 1)
        ):
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        if size > max_bytes:
            continue
        if _detect_binary(entry):
            continue
        yield ScannedFile(
            absolute_path=entry,
            relative_path=rel_path,
            size_bytes=size,
        )
