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

import shutil
import time
from pathlib import Path

# Newest N snapshots of each family to retain by default.
DEFAULT_KEEP = 5

# Sibling DB snapshots written next to the live DB (NOT into backups/) by the
# fk-repair / theory-repair scripts, which never pruned them -- the source of the
# observed multi-GB bloat. Matched as ``<db>.{token}-*`` (anchored to the live DB
# filename), never a bare ``*.bak`` glob.
_SIBLING_BACKUP_TOKENS = ("bak-fkrepair", "bak-theory-repair")


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


def prune_backups_aged(
    backup_dir: Path,
    *,
    prefix: str,
    keep: int = DEFAULT_KEEP,
    max_age_days: float,
    now: float | None = None,
) -> list[Path]:
    """Keep the newest ``keep`` ``prefix*`` entries unconditionally; of the rest,
    delete only those older than ``max_age_days``.

    The keep-N floor is applied BEFORE the age gate, so a freshly written
    pre-repair snapshot always survives even when every older copy is reaped.
    Same safety contract as ``prune_backups``: a blank ``prefix`` / negative
    ``keep`` / negative ``max_age_days`` is refused, and every individual delete
    is failure-soft. Returns the deleted paths. ``now`` defaults to the wall
    clock (injectable for tests).
    """
    if not prefix.strip() or keep < 0 or max_age_days < 0:
        return []
    if not backup_dir.is_dir():
        return []
    cutoff = (now if now is not None else time.time()) - max_age_days * 86400.0

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
            continue  # can't assess age -> leave it alone (fail safe)
        entries.append((mtime, path.name, path))

    entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    deleted: list[Path] = []
    for mtime, _name, path in entries[keep:]:
        if mtime < cutoff and _delete_one(path, None):
            deleted.append(path)
    return deleted


def prune_sibling_db_backups(
    db_path: Path,
    *,
    keep: int = DEFAULT_KEEP,
    max_age_days: float,
    now: float | None = None,
) -> list[Path]:
    """Reap aged sibling DB snapshots written next to the live DB by the
    fk-repair / theory-repair scripts (``<db>.bak-fkrepair-<ts>`` /
    ``<db>.bak-theory-repair-<ts>``) -- which had no retention at all.

    Anchored to the live DB filename + a fixed family allowlist: the match prefix
    is ``f"{db_path.name}.{token}-"``, so it can NEVER touch the live DB itself or
    its ``-wal`` / ``-shm`` companions (none of which start with that prefix), and
    is never a bare ``*.bak`` glob. Keep-newest-N floor + age gate as above.
    """
    parent = db_path.parent
    deleted: list[Path] = []
    for token in _SIBLING_BACKUP_TOKENS:
        deleted.extend(
            prune_backups_aged(
                parent,
                prefix=f"{db_path.name}.{token}-",
                keep=keep,
                max_age_days=max_age_days,
                now=now,
            )
        )
    return deleted
