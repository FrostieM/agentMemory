from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_lite.maintenance.sentinels import discover_sentinel_file


def test_discovers_sentinels_next_to_memory_db(tmp_path: Path) -> None:
    memory_dir = tmp_path / ".agent_memory"
    memory_dir.mkdir()
    db_path = memory_dir / "memory.db"
    sentinel = memory_dir / "retrieval_sentinels.yaml"
    sentinel.write_text("[]", encoding="utf-8")

    discovered = discover_sentinel_file(db_path=db_path)

    assert discovered.path == sentinel.resolve()
    assert discovered.source == "db_dir"
    assert discovered.warnings == []


def test_missing_required_sentinel_reports_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    discovered = discover_sentinel_file(
        db_path=tmp_path / ".agent_memory" / "memory.db",
        require=True,
    )

    assert discovered.path is None
    assert discovered.required is True
    assert discovered.warnings
