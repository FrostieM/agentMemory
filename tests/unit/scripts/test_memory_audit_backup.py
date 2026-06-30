"""Batch J / #122: the audit backup snapshots SQLite but skips the rebuildable
vector store (the ~20GB backup-bloat fix)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.migrations import apply_migrations
from scripts.memory_audit import _backup


def test_backup_snapshots_sqlite_but_skips_rebuildable_vectors(tmp_path: Path) -> None:
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    apply_migrations(conn)
    conn.close()
    vectors = tmp_path / "vectors.lance"
    vectors.mkdir()
    (vectors / "data.bin").write_text("x" * 4096, encoding="utf-8")

    settings = Settings().model_copy(update={"db_path": db, "vector_db_path": vectors})
    out = _backup(settings)
    backups = tmp_path / "backups"

    # SQLite (the source of record) IS snapshotted.
    assert Path(out["db"]).exists()
    assert any("memory_before_audit_repair_" in p.name for p in backups.iterdir())
    # The DERIVED vector store is NOT -- it is rebuildable, so copytree-ing it only
    # bloated the backups dir without adding a recovery point.
    assert "skipped" in out["vectors"]
    assert not any("vectors_before_audit_repair_" in p.name for p in backups.iterdir())
    assert not any(p.name.endswith(".lance") for p in backups.iterdir())
