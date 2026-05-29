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
from agent_memory_lite.maintenance.backup_retention import prune_backups


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
