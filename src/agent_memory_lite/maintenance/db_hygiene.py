"""Periodic SQLite hygiene — WAL checkpoint + occasional VACUUM.

Runs from the brain pass (i.e. on every sentinel-triggered overdue
tick) so a long-running HTTP service doesn't accumulate WAL pages and
fragmented free-pages indefinitely. Failure-soft: any sqlite error is
swallowed and reported in the brain_pass errors list.

* WAL checkpoint — cheap (microseconds on small DB, milliseconds on
  large). Runs every tick. ``PRAGMA wal_checkpoint(TRUNCATE)`` flushes
  the WAL file and truncates it to zero.
* VACUUM — expensive (rewrites the whole DB file). Runs only when
  ``workspace_meta.last_vacuum_at`` is older than
  ``MEMORY_VACUUM_INTERVAL_HOURS`` (default 168h = one week). Vacuum
  reclaims free space from deletes/archives that would otherwise
  remain as fragmented unused pages.

Settings:
* ``MEMORY_DB_HYGIENE_ENABLED`` — master switch (default true).
* ``MEMORY_VACUUM_INTERVAL_HOURS`` — gap between VACUUMs (default 168).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from agent_memory_lite.config import workspace_meta
from agent_memory_lite.utils.time import iso_now, parse_iso

_log = logging.getLogger(__name__)
_LAST_VACUUM_KEY = "last_vacuum_at"


@dataclass(slots=True)
class DbHygieneReport:
    """Per-pass summary."""

    wal_checkpoint_pages: int = 0  # pages reclaimed (-1 on error / disabled)
    vacuum_ran: bool = False
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "wal_checkpoint_pages": self.wal_checkpoint_pages,
            "vacuum_ran": self.vacuum_ran,
            "errors": list(self.errors or []),
        }


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _interval_hours() -> float:
    raw = os.environ.get("MEMORY_VACUUM_INTERVAL_HOURS", "168").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 168.0


def is_enabled() -> bool:
    return _bool_env("MEMORY_DB_HYGIENE_ENABLED", True)


def _vacuum_overdue(conn: sqlite3.Connection, workspace_id: str) -> bool:
    """``True`` when no VACUUM has run within the configured window."""
    last_iso = workspace_meta.get(conn, workspace_id, _LAST_VACUUM_KEY)
    if not last_iso:
        return True
    try:
        last = parse_iso(last_iso)
    except ValueError:
        return True
    elapsed_hours = (datetime.now(UTC) - last).total_seconds() / 3600.0
    return elapsed_hours >= _interval_hours()


_VACUUM_BUSY_COOLDOWN_KEY = "vacuum_busy_cooldown_until"
_VACUUM_BUSY_COOLDOWN_HOURS = 1.0


def _is_busy_error(exc: sqlite3.Error) -> bool:
    """Distinguish lock contention from data errors. SQLite raises
    ``OperationalError("database is locked")`` for busy-conflicts, which
    is a transient runtime state, NOT a data integrity problem."""
    msg = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in msg or "busy" in msg)


def _vacuum_under_busy_cooldown(conn: sqlite3.Connection, workspace_id: str) -> bool:
    """After a SQLITE_BUSY VACUUM failure we wait a cooldown window
    before retrying — otherwise every brain-pass tick re-attempts
    immediately and just stacks up `busy` errors in the report."""
    raw = workspace_meta.get(conn, workspace_id, _VACUUM_BUSY_COOLDOWN_KEY)
    if not raw:
        return False
    try:
        until = parse_iso(raw)
    except ValueError:
        return False
    return datetime.now(UTC) < until


def _record_vacuum_busy(conn: sqlite3.Connection, workspace_id: str) -> None:
    """Stamp a cooldown deadline so the next tick skips VACUUM."""
    from datetime import timedelta  # noqa: PLC0415

    until = (datetime.now(UTC) + timedelta(hours=_VACUUM_BUSY_COOLDOWN_HOURS)).isoformat()
    try:
        workspace_meta.set_value(conn, workspace_id, _VACUUM_BUSY_COOLDOWN_KEY, until)
        conn.commit()
    except sqlite3.Error:  # pragma: no cover - defensive
        pass


def run_db_hygiene(conn: sqlite3.Connection, *, workspace_id: str) -> DbHygieneReport:
    """Run WAL checkpoint + maybe VACUUM. Failure-soft.

    SQLITE_BUSY (lock contention) is tagged distinctly so brain-pass
    reports don't look like data-loss bugs, and a cooldown window
    prevents retry storms on every tick when the DB is contended.
    """
    report = DbHygieneReport(errors=[])
    if not is_enabled():
        report.wal_checkpoint_pages = -1
        return report
    # WAL checkpoint — runs every tick.
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        # PRAGMA wal_checkpoint returns (busy, log_pages, checkpointed_pages).
        report.wal_checkpoint_pages = int(row[2]) if row and len(row) >= 3 else 0
    except sqlite3.Error as exc:
        assert report.errors is not None  # narrow Optional[list] for mypy
        tag = "wal_checkpoint_busy" if _is_busy_error(exc) else "wal_checkpoint"
        report.errors.append(f"{tag}:{exc}")
    # VACUUM — only when overdue + not in cooldown after a recent busy.
    if _vacuum_under_busy_cooldown(conn, workspace_id):
        return report
    try:
        if _vacuum_overdue(conn, workspace_id):
            conn.execute("VACUUM")
            workspace_meta.set_value(conn, workspace_id, _LAST_VACUUM_KEY, iso_now())
            conn.commit()
            report.vacuum_ran = True
    except sqlite3.Error as exc:
        assert report.errors is not None
        if _is_busy_error(exc):
            report.errors.append(f"vacuum_busy:{exc}")
            _record_vacuum_busy(conn, workspace_id)
        else:
            report.errors.append(f"vacuum:{exc}")
    return report
