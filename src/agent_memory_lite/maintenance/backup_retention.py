"""Retention for the ``.agent_memory/backups`` directory.

Background
----------
Several operator scripts (``memory_audit``, ``bulk_index_codebase``,
``memory_auto_triage``, ``memory_encoding_audit``, ``memory_workspace_doctor``,
``repair_dangling_source_refs``) snapshot the SQLite DB -- and a couple also
``copytree`` the whole ``vectors.lance`` store -- into ``<db_dir>/backups``
before a risky operation. None pruned old snapshots, so the directory grew
without bound (observed: ~20GB of repeated ``vectors_*.lance`` + ``memory_*.db``
copies). See issue ``issue_201c0b47be474319``.

This module is the single retention primitive those creators call after writing
a fresh backup: keep the newest ``keep`` snapshots of a given family, delete the
rest.

Safety
------
``prune_backups`` only ever deletes direct children of ``backup_dir`` whose name
starts with the explicit ``prefix`` the caller passes (its own backup family).
An empty ``prefix`` is refused (it would match everything), a negative ``keep``
is a no-op, and every individual delete is failure-soft -- a locked or vanished
entry is skipped, never aborting the sweep, and never escaping as an exception
into a startup/repair path.
"""

from __future__ import annotations

from pathlib import Path

from agent_memory_lite.maintenance.backup_retention_aged import (
    prune_backups_aged,
    prune_sibling_db_backups,
)
from agent_memory_lite.maintenance.backup_retention_common import DEFAULT_KEEP, _delete_one


def prune_backups(
    backup_dir: Path,
    *,
    prefix: str,
    keep: int = DEFAULT_KEEP,
    protect: Path | None = None,
) -> list[Path]:
    """Keep the newest ``keep`` entries named ``prefix*`` in ``backup_dir``; delete older.

    ``protect`` (the caller's just-written backup) is NEVER deleted, regardless
    of sort order -- so a creator that prunes right after taking a snapshot can
    never delete the very recovery point it just made, even when ``shutil.copy2``
    has propagated an old source mtime onto the fresh copy or the wall clock has
    regressed.

    Returns the list of deleted paths. Never raises: on any filesystem error the
    offending entry is skipped. Refuses to act on a blank ``prefix`` (which would
    match every file) or a negative ``keep``.
    """
    if not prefix.strip() or keep < 0:
        return []
    if not backup_dir.is_dir():
        return []

    protect_resolved: Path | None = None
    if protect is not None:
        try:
            protect_resolved = protect.resolve()
        except OSError:
            protect_resolved = None

    entries: list[tuple[float, str, Path]] = []
    try:
        children = list(backup_dir.iterdir())
    except OSError:
        return []
    for path in children:
        if not path.name.startswith(prefix):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            # Can't assess age -> leave it alone (fail safe).
            continue
        entries.append((mtime, path.name, path))

    # Newest first; name is a deterministic tie-break (timestamped names sort
    # chronologically), so equal-mtime entries prune predictably.
    entries.sort(key=lambda item: (item[0], item[1]), reverse=True)

    deleted: list[Path] = []
    for _mtime, _name, path in entries[keep:]:
        if _delete_one(path, protect_resolved):
            deleted.append(path)
    return deleted


__all__ = [
    "DEFAULT_KEEP",
    "prune_backups",
    "prune_backups_aged",
    "prune_sibling_db_backups",
]
