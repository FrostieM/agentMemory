"""Compute the diff between two ``memory_state_snapshots`` rows.

Pure function over two ``MemoryStateSnapshot`` records. Reports
counts deltas, added / removed item ids, and content-changed ids
(same id, different short hash).
"""

from __future__ import annotations

from agent_memory_lite.models.memory_state_snapshots import (
    MemoryStateSnapshot,
    MemoryStateSnapshotDiff,
)


def diff_state_snapshots(
    before: MemoryStateSnapshot,
    after: MemoryStateSnapshot,
) -> MemoryStateSnapshotDiff:
    counts_delta: dict[str, int] = {}
    keys = set(before.counts) | set(after.counts)
    for key in sorted(keys):
        before_count = int(before.counts.get(key, 0))
        after_count = int(after.counts.get(key, 0))
        delta = after_count - before_count
        if delta != 0:
            counts_delta[key] = delta

    before_keys = set(before.digests)
    after_keys = set(after.digests)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    shared = before_keys & after_keys
    changed = sorted(key for key in shared if before.digests[key] != after.digests[key])

    return MemoryStateSnapshotDiff(
        before_snapshot_id=before.id,
        after_snapshot_id=after.id,
        before_taken_at=before.taken_at,
        after_taken_at=after.taken_at,
        counts_delta=counts_delta,
        added=added,
        removed=removed,
        changed=changed,
    )
