"""Path normalization helpers.

Windows backslashes, mixed separators and `..` segments all get folded into a
single canonical absolute string. Used for both filesystem operations and stable
SQLite keys (`files.path`).
"""

from __future__ import annotations

from pathlib import Path


def normalize_path(path: str | Path, *, base: str | Path | None = None) -> str:
    """Return an absolute, forward-slashed path string.

    If `base` is given and `path` is relative, the result is resolved against `base`.
    Symlinks are NOT resolved (we keep the user's chosen layout).
    """
    p = Path(path)
    if not p.is_absolute() and base is not None:
        p = Path(base) / p
    p = p.expanduser()
    if not p.is_absolute():
        p = p.resolve()
    return str(p).replace("\\", "/")


def to_workspace_relative(absolute_path: str | Path, workspace_root: str | Path) -> str:
    """Return a forward-slashed path relative to `workspace_root`.

    Raises ValueError if the path falls outside the workspace.
    """
    abs_p = Path(absolute_path).expanduser().resolve()
    root = Path(workspace_root).expanduser().resolve()
    return str(abs_p.relative_to(root)).replace("\\", "/")
