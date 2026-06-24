"""Shared primitives for backup retention.

Holds the default keep-count and the failure-soft single-entry delete used by
both the plain newest-N sweep (``backup_retention``) and the age-gated sweeps
(``backup_retention_aged``). Extracted into a dependency-free common module so
both siblings import these without forming an import cycle.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Newest N snapshots of each family to retain by default.
DEFAULT_KEEP = 5


def _delete_one(path: Path, protect_resolved: Path | None) -> bool:
    """Delete one backup entry; return True iff removed. Never raises.

    Skips the caller-protected path and is failure-soft on any OS error
    (locked / already-gone / permission fault / rmtree-on-symlink).
    """
    if protect_resolved is not None:
        try:
            if path.resolve() == protect_resolved:
                return False  # never delete the caller's fresh backup
        except OSError:
            pass
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError:
        return False
    return True
