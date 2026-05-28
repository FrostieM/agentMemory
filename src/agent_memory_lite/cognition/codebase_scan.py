"""Shared source-file scan helpers for code-memory indexing and audit."""

from __future__ import annotations

import hashlib
from pathlib import Path

# File extensions we consider source code worth digesting. Keep this shared
# between the bulk indexer and freshness audit so they measure the same surface.
DEFAULT_CODE_EXTENSIONS = frozenset(
    {".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".md", ".yml", ".yaml", ".toml", ".ps1", ".sh"}
)

# Directories we never descend into while building code memory.
DEFAULT_CODE_SKIP_DIRS = frozenset(
    {
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".agent_memory",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "htmlcov",
        ".tox",
        ".idea",
        ".vscode",
        ".claude",
        "futureDesign",
        "logs",
        "reports",
        "tmp-ui-shots",
        "tmp",
        "coverage",
    }
)


def iter_source_files(
    project_root: Path,
    *,
    extensions: frozenset[str] = DEFAULT_CODE_EXTENSIONS,
    skip_dirs: frozenset[str] = DEFAULT_CODE_SKIP_DIRS,
) -> list[Path]:
    """Walk ``project_root`` and return every file with an indexable extension."""
    files: list[Path] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel_parts = path.relative_to(project_root).parts
        except ValueError:
            continue
        if any(part in skip_dirs for part in rel_parts):
            continue
        if path.suffix.lower() in extensions:
            files.append(path)
    return sorted(files)


def stored_path(project_root: Path, file_path: Path, *, relative_paths: bool = True) -> str:
    """Return the canonical path key used in ``code_digests.file_path``."""
    if relative_paths:
        return str(file_path.relative_to(project_root)).replace("\\", "/")
    return str(file_path)


def source_file_sha1(path: Path) -> str:
    """Compute the same text SHA1 used by ``code_indexer.index_file``."""
    content = path.read_text(encoding="utf-8", errors="replace")
    return hashlib.sha1(
        content.encode("utf-8", errors="replace"),
        usedforsecurity=False,
    ).hexdigest()
