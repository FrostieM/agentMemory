from __future__ import annotations

import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path
from types import ModuleType


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[3] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_sqlite_db_captures_committed_wal_rows(tmp_path: Path) -> None:
    script = _load_script("memory_backup_restore_check.py")
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO items (value) VALUES ('from-wal')")
        conn.commit()
        assert (tmp_path / "source.db-wal").exists()

    script._backup_sqlite_db(source, target)

    with closing(sqlite3.connect(target)) as conn:
        rows = conn.execute("SELECT value FROM items").fetchall()
    assert rows == [("from-wal",)]
