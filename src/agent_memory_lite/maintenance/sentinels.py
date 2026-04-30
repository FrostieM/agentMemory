"""Discovery helpers for project-local retrieval sentinel files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_SENTINEL_NAME = "retrieval_sentinels.yaml"


@dataclass(frozen=True, slots=True)
class SentinelDiscovery:
    path: Path | None
    source: str
    required: bool
    warnings: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path) if self.path is not None else None,
            "source": self.source,
            "required": self.required,
            "warnings": list(self.warnings),
        }


def _candidate_paths(
    *,
    db_path: Path | None,
    project_root: Path | None,
) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    if project_root is not None:
        candidates.append(
            (project_root.resolve() / ".agent_memory" / DEFAULT_SENTINEL_NAME, "project_root")
        )
    if db_path is not None and str(db_path) != ":memory:":
        candidates.append((db_path.resolve().parent / DEFAULT_SENTINEL_NAME, "db_dir"))
    candidates.append((Path.cwd().resolve() / ".agent_memory" / DEFAULT_SENTINEL_NAME, "cwd"))

    seen: set[Path] = set()
    unique: list[tuple[Path, str]] = []
    for path, source in candidates:
        if path in seen:
            continue
        seen.add(path)
        unique.append((path, source))
    return unique


def discover_sentinel_file(
    *,
    explicit_path: str | Path | None = None,
    db_path: str | Path | None = None,
    project_root: str | Path | None = None,
    require: bool = False,
) -> SentinelDiscovery:
    """Find a retrieval sentinel file without requiring every CLI caller to pass it."""

    warnings: list[str] = []
    if explicit_path:
        path = Path(explicit_path).resolve()
        if not path.exists():
            message = f"sentinel file not found: {path}"
            if require:
                warnings.append(message)
            return SentinelDiscovery(
                path=None, source="explicit_missing", required=True, warnings=warnings
            )
        return SentinelDiscovery(path=path, source="explicit", required=True, warnings=[])

    resolved_db = Path(db_path).resolve() if db_path and str(db_path) != ":memory:" else None
    resolved_root = Path(project_root).resolve() if project_root else None
    for path, source in _candidate_paths(db_path=resolved_db, project_root=resolved_root):
        if path.exists():
            return SentinelDiscovery(path=path, source=source, required=require, warnings=[])

    if require:
        locations = [
            str(path)
            for path, _source in _candidate_paths(db_path=resolved_db, project_root=resolved_root)
        ]
        warnings.append(f"no retrieval sentinel file found in: {locations}")
    return SentinelDiscovery(path=None, source="not_found", required=require, warnings=warnings)
