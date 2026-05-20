"""Warn-once-per-process helper for ``_runtime.db_for`` registry misses.

When the MCP stdio server falls back to its anchor connection because
``workspace_id`` is not in the registry, that fallback is silent in the
hot path: the operator gets wrong-empty reads / wrong-target writes
without any signal. The fix is a single warning per unresolved id —
loud enough to surface in service logs, quiet enough not to spam.

Lifted out of ``stdio_runtime.py`` to keep that module under the
150-SLOC ceiling.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

_log = logging.getLogger(__name__)
# Per-process set of workspace_ids the registry could not resolve. Used
# to log a misconfiguration warning ONCE per id; tests reset via
# ``reset_unresolved_warned``.
# Round-2 audit: same warn-once race as ``_hub_write_warned`` — lock
# the check-and-add so concurrent fallbacks don't double-emit.
_unresolved_warned: set[str] = set()
_UNRESOLVED_WARNED_LOCK = threading.Lock()


def reset_unresolved_warned() -> None:
    """Test hook: clear the warn-once set for db_for fallbacks."""
    with _UNRESOLVED_WARNED_LOCK:
        _unresolved_warned.clear()


def warn_unresolved_once(
    *,
    workspace_id: str,
    anchor_id: str,
    workspaces_file: str | Path,
    anchor_db_path: str | Path,
) -> None:
    """Emit a warning the first time ``workspace_id`` falls back to anchor.

    Skipped when ``workspace_id == anchor_id`` (a caller passing the
    anchor back is by definition not misconfigured) or when this
    process has already warned for the same id.
    """
    if workspace_id == anchor_id:
        return
    # Atomic check-and-add so a concurrent fallback can't slip past us
    # and emit a duplicate warning for the same workspace_id.
    with _UNRESOLVED_WARNED_LOCK:
        if workspace_id in _unresolved_warned:
            return
        _unresolved_warned.add(workspace_id)
    _log.warning(
        "db_for: workspace_id=%r not in registry %s; "
        "falling back to anchor=%r (db=%s). Misconfigured? "
        "Register via scripts/register_workspace.py or check "
        "MEMORY_DB_PATH / MEMORY_WORKSPACE_ID env.",
        workspace_id,
        workspaces_file,
        anchor_id,
        anchor_db_path,
    )
