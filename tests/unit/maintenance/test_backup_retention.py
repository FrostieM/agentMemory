"""Backup-directory retention (release plan step 10.9).

``prune_backups`` caps the unbounded growth of ``.agent_memory/backups`` (issue
issue_201c0b47be474319) by keeping the newest N snapshots of a family and
deleting the rest. These tests pin the delete-SAFETY guarantees: it only ever
touches entries matching the caller's explicit prefix, refuses an empty prefix,
handles both file (.db) and directory (.lance) snapshots, and is failure-soft.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_memory_lite.maintenance import backup_retention as br
from agent_memory_lite.maintenance.backup_retention import (
    prune_backups,
    prune_backups_aged,
    prune_sibling_db_backups,
)

_NOW = 1_000_000.0  # fixed clock for the age-gated tests
_DAY = 86_400.0


def _mk(path: Path, mtime: float, *, is_dir: bool = False) -> Path:
    if is_dir:
        path.mkdir(parents=True)
        (path / "data").write_text("x", encoding="utf-8")
    else:
        path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_keeps_newest_n_and_deletes_older(tmp_path: Path) -> None:
    pre = "memory_before_audit_repair_"
    for i in range(5):
        _mk(tmp_path / f"{pre}{i}.db", mtime=1000.0 + i)  # i=4 newest
    deleted = prune_backups(tmp_path, prefix=pre, keep=2)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == [f"{pre}3.db", f"{pre}4.db"]  # two newest kept
    assert len(deleted) == 3


def test_only_touches_matching_prefix(tmp_path: Path) -> None:
    pre = "memory_before_audit_repair_"
    _mk(tmp_path / f"{pre}0.db", mtime=1000.0)
    other = _mk(tmp_path / "vectors_before_audit_repair_0.lance", mtime=1001.0, is_dir=True)
    unrelated = _mk(tmp_path / "important_user_file.db", mtime=1002.0)
    # keep=0 would delete ALL matching, but only the matching family.
    deleted = prune_backups(tmp_path, prefix=pre, keep=0)
    assert deleted == [tmp_path / f"{pre}0.db"]
    assert other.exists()  # different prefix -> untouched
    assert unrelated.exists()  # non-backup file -> untouched


def test_empty_prefix_is_refused(tmp_path: Path) -> None:
    # Critical safety: an empty prefix would match everything -> must no-op.
    f = _mk(tmp_path / "anything.db", mtime=1000.0)
    assert prune_backups(tmp_path, prefix="", keep=0) == []
    assert f.exists()


def test_negative_keep_is_noop(tmp_path: Path) -> None:
    f = _mk(tmp_path / "memory_x_0.db", mtime=1000.0)
    assert prune_backups(tmp_path, prefix="memory_x_", keep=-1) == []
    assert f.exists()


def test_keep_ge_count_deletes_nothing(tmp_path: Path) -> None:
    pre = "memory_x_"
    for i in range(3):
        _mk(tmp_path / f"{pre}{i}.db", mtime=1000.0 + i)
    assert prune_backups(tmp_path, prefix=pre, keep=10) == []
    assert len(list(tmp_path.iterdir())) == 3


def test_prunes_lance_directories(tmp_path: Path) -> None:
    pre = "vectors_before_audit_repair_"
    for i in range(3):
        _mk(tmp_path / f"{pre}{i}.lance", mtime=1000.0 + i, is_dir=True)
    deleted = prune_backups(tmp_path, prefix=pre, keep=1)
    assert len(deleted) == 2
    remaining = [p.name for p in tmp_path.iterdir()]
    assert remaining == [f"{pre}2.lance"]  # newest dir kept, rmtree'd the rest


def test_families_are_independent(tmp_path: Path) -> None:
    for i in range(3):
        _mk(tmp_path / f"memory_before_audit_repair_{i}.db", mtime=1000.0 + i)
        _mk(tmp_path / f"vectors_before_audit_repair_{i}.lance", mtime=2000.0 + i, is_dir=True)
    prune_backups(tmp_path, prefix="memory_before_audit_repair_", keep=1)
    # The vectors family is untouched by pruning the memory family.
    assert sum(1 for p in tmp_path.iterdir() if p.name.startswith("vectors_")) == 3
    assert sum(1 for p in tmp_path.iterdir() if p.name.startswith("memory_")) == 1


def test_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert prune_backups(tmp_path / "nope", prefix="x_", keep=2) == []


def test_protect_path_is_never_deleted(tmp_path: Path) -> None:
    # Identical mtime across the family (simulates shutil.copy2 copying the
    # source DB's mtime onto every backup) so ordering falls to the name
    # tie-break. The freshly-made backup `aaa` sorts INTO the delete range by
    # name, but protect= must save it -- the recovery point a creator just made
    # can never be pruned in the same run.
    pre = "memory_before_audit_repair_"
    fresh = _mk(tmp_path / f"{pre}aaa.db", mtime=5000.0)  # sorts last -> delete range
    _mk(tmp_path / f"{pre}bbb.db", mtime=5000.0)
    _mk(tmp_path / f"{pre}ccc.db", mtime=5000.0)  # newest by name -> kept
    deleted = prune_backups(tmp_path, prefix=pre, keep=1, protect=fresh)
    assert fresh.exists()  # protected despite sorting into the delete range
    assert (tmp_path / f"{pre}ccc.db").exists()
    assert deleted == [tmp_path / f"{pre}bbb.db"]  # only the unprotected old one


def test_blank_prefix_is_refused(tmp_path: Path) -> None:
    # Whitespace-only prefix would match space-prefixed names; refuse it too.
    f = _mk(tmp_path / "anything.db", mtime=1000.0)
    assert prune_backups(tmp_path, prefix="   ", keep=0) == []
    assert f.exists()


def test_failure_soft_skips_unremovable_entry(tmp_path: Path, monkeypatch) -> None:
    pre = "vectors_x_"
    for i in range(3):
        _mk(tmp_path / f"{pre}{i}.lance", mtime=1000.0 + i, is_dir=True)
    real_rmtree = br.shutil.rmtree

    def _flaky_rmtree(path, *a, **k):
        if path.name.endswith("0.lance"):  # the oldest -> simulate a lock
            raise OSError("locked")
        return real_rmtree(path, *a, **k)

    monkeypatch.setattr(br.shutil, "rmtree", _flaky_rmtree)
    # keep=1 -> wants to delete index 0 and 1; index 0 fails, 1 succeeds.
    deleted = prune_backups(tmp_path, prefix=pre, keep=1)
    names = sorted(p.name for p in deleted)
    assert names == [f"{pre}1.lance"]  # the failure did not abort the sweep
    assert (tmp_path / f"{pre}0.lance").exists()  # skipped, still present


# ============================================================
# Phase-4: age-gated prune + sibling DB-snapshot sweep
# ============================================================


def test_aged_keeps_young_beyond_floor(tmp_path: Path) -> None:
    """The age gate keeps a file that is beyond the newest-N floor but younger
    than max_age_days; only the aged-out extras are reaped."""
    pre = "memory.db.bak-fkrepair-"
    _mk(tmp_path / f"{pre}old0", mtime=_NOW - 5 * _DAY)
    _mk(tmp_path / f"{pre}old1", mtime=_NOW - 4 * _DAY)
    young = _mk(tmp_path / f"{pre}young", mtime=_NOW - 0.5 * _DAY)  # within 1 day
    newest = _mk(tmp_path / f"{pre}newest", mtime=_NOW)
    deleted = prune_backups_aged(tmp_path, prefix=pre, keep=1, max_age_days=1, now=_NOW)
    assert newest.exists()  # keep-1 floor
    assert young.exists()  # beyond floor but young -> kept by the age gate
    assert {p.name for p in deleted} == {f"{pre}old0", f"{pre}old1"}


def test_aged_keep_floor_wins_over_age(tmp_path: Path) -> None:
    """keep-N is unconditional: 5 ancient files, keep=5 -> nothing deleted."""
    pre = "memory.db.bak-fkrepair-"
    for i in range(5):
        _mk(tmp_path / f"{pre}{i}", mtime=_NOW - 999 * _DAY)
    deleted = prune_backups_aged(tmp_path, prefix=pre, keep=5, max_age_days=14, now=_NOW)
    assert deleted == []
    assert len(list(tmp_path.iterdir())) == 5


def test_sibling_sweep_reaps_aged_never_touches_live_db(tmp_path: Path) -> None:
    """The sibling sweep reaps aged .bak-fkrepair-/.bak-theory-repair- snapshots
    and NEVER touches the live DB, its -wal/-shm companions, or an unrelated db."""
    # OLD mtimes on the live store: if the filename anchor ever regressed, the
    # age gate would reap these -- so their survival proves the ANCHOR (dot-vs-dash
    # at the family prefix), not the age check, is what protects the live DB.
    db = _mk(tmp_path / "memory.db", mtime=_NOW - 999 * _DAY)
    wal = _mk(tmp_path / "memory.db-wal", mtime=_NOW - 999 * _DAY)
    shm = _mk(tmp_path / "memory.db-shm", mtime=_NOW - 999 * _DAY)
    unrelated = _mk(tmp_path / "foo.db", mtime=_NOW - 999 * _DAY)
    fk_old0 = _mk(tmp_path / "memory.db.bak-fkrepair-001", mtime=_NOW - 5 * _DAY)
    fk_old1 = _mk(tmp_path / "memory.db.bak-fkrepair-002", mtime=_NOW - 4 * _DAY)
    fk_new = _mk(tmp_path / "memory.db.bak-fkrepair-003", mtime=_NOW - 0.5 * _DAY)
    th_only = _mk(tmp_path / "memory.db.bak-theory-repair-001", mtime=_NOW - 9 * _DAY)

    deleted = prune_sibling_db_backups(db, keep=1, max_age_days=1, now=_NOW)

    # live store + companions + unrelated file are inviolate
    assert db.exists()
    assert wal.exists()
    assert shm.exists()
    assert unrelated.exists()
    # fkrepair: keep the newest (floor), reap the two aged
    assert fk_new.exists()
    assert {p.name for p in deleted} == {fk_old0.name, fk_old1.name}
    # theory-repair has a single (newest) entry -> kept by the keep-1 floor
    assert th_only.exists()


def test_sibling_sweep_failure_soft(tmp_path: Path, monkeypatch) -> None:
    db = _mk(tmp_path / "memory.db", mtime=_NOW)
    bak = _mk(tmp_path / "memory.db.bak-fkrepair-001", mtime=_NOW - 99 * _DAY)

    def _boom(self, *a, **k):
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", _boom)
    # keep=0 -> wants to delete the aged sibling, but unlink raises; must not escape.
    assert prune_sibling_db_backups(db, keep=0, max_age_days=1, now=_NOW) == []
    assert bak.exists()  # skipped, still present
