"""Durable code-digest refresh: recompute stale digests in the background.

The PostToolUse queue worker (``cognition/digest_worker``) refreshes a digest
when the agent edits a file through the hook. But a queue drop, an edit made
outside the hook, or the 120s pre-commit ingest ceiling can leave a digest
whose ``file_sha1`` no longer matches the file on disk -- ``impact_check`` then
returns a stale verdict (observed: ``is_stale=true, sha_mismatch``). This module
is the catch-up: each brain pass it checks a BOUNDED batch of the
least-recently-verified digests and recomputes the stale ones, so staleness
self-heals without relying on the queue.

Bounded + rotating: at most ``limit`` digests per call, ordered by
``last_indexed_at`` ascending, and the timestamp is bumped on every digest
checked (fresh, stale, or unreadable), so each call advances to the next batch
and the whole set is covered over successive passes (which assumes passes land
in distinct ``last_indexed_at`` timestamps -- true at the multi-hour brain-pass
cadence) -- O(limit) file reads per pass, never an O(repo) tree walk.
Failure-soft, with bounded retries on
transient read / DB-lock errors. Missing-file (orphan) and never-indexed-file
detection stay with the heavier audit scan (``code_memory_freshness``); this
loop only re-verifies digests that already exist.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from agent_memory_lite.cognition.digest_worker import compute_digest, upsert_digest
from agent_memory_lite.maintenance.integrity_models import table_exists

DEFAULT_REFRESH_LIMIT = 50
DEFAULT_MAX_RETRIES = 2


@dataclass(slots=True)
class DigestRefreshStats:
    """Per-pass refresh summary."""

    checked: int = 0
    refreshed: int = 0
    verified: int = 0
    unreadable: int = 0
    failed: int = 0


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _resolve(project_root: Path, stored_path: str) -> Path:
    candidate = Path(stored_path)
    return candidate if candidate.is_absolute() else project_root / candidate


def _read_with_retry(path: Path, max_retries: int) -> str | None:
    """Read a file's text, retrying transient OS errors. None if unreadable."""
    for attempt in range(max_retries + 1):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, ValueError):
            # Missing file won't reappear; ValueError = pathological path
            # (e.g. embedded NUL). Neither is retryable -- treat as unreadable.
            return None
        except OSError:
            if attempt >= max_retries:
                return None
    return None


def _touch(conn: sqlite3.Connection, workspace_id: str, file_path: str, now: str) -> None:
    """Bump last_indexed_at so this digest rotates to the back of the queue."""
    with contextlib.suppress(sqlite3.Error):
        conn.execute(
            "UPDATE code_digests SET last_indexed_at = ? WHERE workspace_id = ? AND file_path = ?",
            (now, workspace_id, file_path),
        )


def _upsert_with_retry(
    conn: sqlite3.Connection, workspace_id: str, result: object, max_retries: int
) -> bool:
    for attempt in range(max_retries + 1):
        try:
            upsert_digest(conn, workspace_id=workspace_id, result=result)  # type: ignore[arg-type]
            return True
        except sqlite3.OperationalError:
            if attempt >= max_retries:
                return False  # likely a persistent lock; give up this pass
        except sqlite3.Error:
            return False
    return False


def refresh_stale_digests(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    project_root: Path | None,
    limit: int = DEFAULT_REFRESH_LIMIT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> DigestRefreshStats:
    """Re-verify the ``limit`` least-recently-checked digests; recompute the stale.

    ``project_root`` resolves project-relative digest paths to disk; when it is
    None (unknown source root) the pass is a no-op. Bounded, rotating, and
    failure-soft -- never raises.
    """
    stats = DigestRefreshStats()
    if project_root is None or limit <= 0:
        return stats
    try:
        # table_exists is inside the try so a closed / corrupt connection
        # (ProgrammingError, also an sqlite3.Error) is swallowed too -- the
        # function's "never raises" contract holds independent of the caller.
        if not table_exists(conn, "code_digests"):
            return stats
        rows = conn.execute(
            "SELECT file_path, file_sha1 FROM code_digests WHERE workspace_id = ? "
            "ORDER BY last_indexed_at ASC, file_path ASC LIMIT ?",
            (workspace_id, int(limit)),
        ).fetchall()
    except sqlite3.Error:
        return stats

    now = _now()
    for row in rows:
        file_path = str(row["file_path"] if isinstance(row, sqlite3.Row) else row[0])
        stored_sha1 = str((row["file_sha1"] if isinstance(row, sqlite3.Row) else row[1]) or "")
        stats.checked += 1
        content = _read_with_retry(_resolve(project_root, file_path), max_retries)
        if content is None:
            stats.unreadable += 1
            _touch(conn, workspace_id, file_path, now)
            continue
        try:
            result = compute_digest(file_path, content)
        except Exception:
            stats.failed += 1
            _touch(conn, workspace_id, file_path, now)
            continue
        if result.file_sha1 == stored_sha1:
            stats.verified += 1
            _touch(conn, workspace_id, file_path, now)
        elif _upsert_with_retry(conn, workspace_id, result, max_retries):
            stats.refreshed += 1  # upsert_digest bumps last_indexed_at itself
        else:
            stats.failed += 1
            _touch(conn, workspace_id, file_path, now)

    with contextlib.suppress(sqlite3.Error):
        conn.commit()
    return stats
