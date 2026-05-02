"""Exclude rules for workspace ingestion.

Layered: builtin denylist + `.gitignore` patterns + optional `.memoryignore`.
Uses fnmatch-style matching against the workspace-relative POSIX path. The
patterns supported are a subset of git's: literal names, `*` and `?`
wildcards, leading `/` to anchor at workspace root, and trailing `/` to mean
"directory only". Negation patterns (`!pattern`) are honoured.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

BUILTIN_DENYLIST: tuple[str, ...] = (
    ".git/",
    "node_modules/",
    "__pycache__/",
    ".venv/",
    "venv/",
    "dist/",
    "build/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".hypothesis/",
    "*.min.js",
    "*.lock",
    "*.lance/",
)

DEFAULT_MAX_FILE_BYTES = 1_048_576  # 1 MB


@dataclass(frozen=True, slots=True)
class ExcludePattern:
    pattern: str
    negate: bool


def _parse_lines(lines: list[str]) -> list[ExcludePattern]:
    out: list[ExcludePattern] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = False
        if line.startswith("!"):
            negate = True
            line = line[1:]
        out.append(ExcludePattern(pattern=line, negate=negate))
    return out


def load_pattern_file(path: Path) -> list[ExcludePattern]:
    if not path.exists() or not path.is_file():
        return []
    return _parse_lines(path.read_text(encoding="utf-8").splitlines())


def collect_patterns(
    workspace_root: Path,
    *,
    extra: list[str] | None = None,
) -> list[ExcludePattern]:
    patterns: list[ExcludePattern] = [ExcludePattern(p, False) for p in BUILTIN_DENYLIST]
    patterns.extend(load_pattern_file(workspace_root / ".gitignore"))
    patterns.extend(load_pattern_file(workspace_root / ".memoryignore"))
    if extra:
        patterns.extend(_parse_lines(extra))
    return patterns


def _matches(pattern: str, rel_path: str, *, is_dir: bool) -> bool:
    p = pattern
    if p.startswith("/"):
        p = p[1:]
        anchored = True
    else:
        anchored = False
    if p.endswith("/"):
        if not is_dir:
            base = rel_path.split("/", maxsplit=1)[0]
            return fnmatch.fnmatch(base + "/", p)
        p_clean = p.rstrip("/")
        if anchored:
            return fnmatch.fnmatch(rel_path, p_clean) or fnmatch.fnmatch(rel_path, p_clean + "/*")
        return any(fnmatch.fnmatch(part, p_clean) for part in [rel_path, *rel_path.split("/")])
    if anchored:
        return fnmatch.fnmatch(rel_path, p)
    if "/" in p:
        return fnmatch.fnmatch(rel_path, p)
    return fnmatch.fnmatch(rel_path, p) or fnmatch.fnmatch(rel_path.rsplit("/", maxsplit=1)[-1], p)


def is_excluded(
    rel_path: str,
    *,
    is_dir: bool,
    patterns: list[ExcludePattern],
) -> bool:
    excluded = False
    for entry in patterns:
        if _matches(entry.pattern, rel_path, is_dir=is_dir):
            excluded = not entry.negate
    return excluded
