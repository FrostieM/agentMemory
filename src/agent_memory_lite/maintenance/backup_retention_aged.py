"""Age-gated retention helpers for backup snapshots.

This sibling of ``backup_retention`` holds the *age-aware* sweeps -- the
keep-newest-N floor combined with a ``max_age_days`` gate -- and the
sibling-DB family allowlist they reap. The plain newest-N primitive
(``prune_backups``) stays in ``backup_retention``; the shared keep-count and the
low-level delete (``DEFAULT_KEEP`` / ``_delete_one``) live in
``backup_retention_common``. Both functions here are re-exported from
``backup_retention`` so the public import surface is unchanged.
"""

from __future__ import annotations

import time
from pathlib import Path

from agent_memory_lite.maintenance.backup_retention_common import DEFAULT_KEEP, _delete_one

# Sibling DB snapshots written next to the live DB (NOT into backups/) by the
# fk-repair / theory-repair scripts, which never pruned them -- the source of the
# observed multi-GB bloat. Matched as ``<db>.{token}-*`` (anchored to the live DB
# filename), never a bare ``*.bak`` glob.
_SIBLING_BACKUP_TOKENS = ("bak-fkrepair", "bak-theory-repair")


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
