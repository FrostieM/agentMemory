"""Unit tests for scripts/memory_consolidation_runner.py.

Covers:

* Registry loading (missing file, malformed JSON, structured payload).
* ``_run_one`` happy path against a real v3-schema DB.
* ``_run_one`` error path returns structured error dict, never raises.
* End-to-end ``main()`` sweeps every registered workspace.
* ``--workspace`` filter limits to one workspace.
* JSON output mode emits one JSON line per workspace.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from scripts import memory_consolidation_runner as runner


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(conn)
    conn.commit()
    conn.close()


def _seed_episode(db_path: Path, *, workspace_id: str, raw_text: str, episode_id: str) -> None:
    conn = sqlite3.connect(db_path)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    conn.execute(
        """
        INSERT INTO episodes
            (id, workspace_id, source_type, raw_text, gist, trust_level,
             importance, confidence, created_at, is_archived)
        VALUES (?, ?, 'agent_action', ?, ?, 'agent_observed', 0.5, 0.5, ?, 0)
        """,
        (episode_id, workspace_id, raw_text, raw_text[:200], created),
    )
    conn.commit()
    conn.close()


def _write_registry(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"version": 1, "workspaces": entries}), encoding="utf-8")


# ============================================================
# Registry loading
# ============================================================


def test_load_registry_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert runner._load_registry(tmp_path / "nope.json") == []


def test_load_registry_returns_empty_for_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    assert runner._load_registry(path) == []


def test_load_registry_parses_workspaces_array(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    _write_registry(path, [{"id": "a", "db_path": "a.db"}, {"id": "b", "db_path": "b.db"}])
    result = runner._load_registry(path)
    assert len(result) == 2
    assert result[0]["id"] == "a"


# ============================================================
# _run_one
# ============================================================


def test_run_one_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # v3.7: consolidate_workspace skips persisting an insight whose only
    # summary is the word-frequency heuristic fallback ("Recurring theme
    # (N episodes): ..."). Force the LLM distiller to return a real
    # "Pattern:" summary so the happy-path insight actually lands —
    # mirrors the llm_pattern fixture used by the consolidation unit
    # tests.
    monkeypatch.setattr(
        "agent_memory_lite.cognition.consolidation_llm.llm_distill_cluster",
        lambda **_kw: "Pattern: a real distilled consolidation insight.",
    )
    monkeypatch.setenv("MEMORY_CONSOLIDATION_LLM_ENABLED", "true")
    db = tmp_path / "ws.db"
    _make_db(db)
    _seed_episode(db, workspace_id="ws", raw_text="kelly sizing review one", episode_id="e1")
    _seed_episode(db, workspace_id="ws", raw_text="kelly sizing review two", episode_id="e2")
    result = runner._run_one(workspace_id="ws", db_path=str(db), window_hours=24, max_insights=10)
    assert result["status"] == "ok"
    assert result["episodes_seen"] == 2
    assert result["insights_written"] == 1


def test_run_one_error_path_returns_dict(tmp_path: Path) -> None:
    # DB path doesn't point to a real DB.
    result = runner._run_one(
        workspace_id="ws", db_path="/does/not/exist.db", window_hours=24, max_insights=10
    )
    assert result["status"] == "error"
    assert "error" in result


# ============================================================
# End-to-end main()
# ============================================================


def test_main_sweeps_all_workspaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    registry = tmp_path / "registry.json"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    _make_db(db_a)
    _make_db(db_b)
    _seed_episode(db_a, workspace_id="ws_a", raw_text="kelly sizing one", episode_id="ea1")
    _seed_episode(db_a, workspace_id="ws_a", raw_text="kelly sizing two", episode_id="ea2")
    _write_registry(
        registry,
        [
            {"id": "ws_a", "db_path": str(db_a)},
            {"id": "ws_b", "db_path": str(db_b)},
        ],
    )
    monkeypatch.setattr(runner, "_registry_path", lambda: registry)
    rc = runner.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[ws_a]" in out
    assert "[ws_b]" in out


def test_main_filters_to_one_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    registry = tmp_path / "registry.json"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    _make_db(db_a)
    _make_db(db_b)
    _write_registry(
        registry,
        [
            {"id": "ws_a", "db_path": str(db_a)},
            {"id": "ws_b", "db_path": str(db_b)},
        ],
    )
    monkeypatch.setattr(runner, "_registry_path", lambda: registry)
    rc = runner.main(["--workspace", "ws_a"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[ws_a]" in out
    assert "[ws_b]" not in out


def test_main_json_mode_emits_one_json_per_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    registry = tmp_path / "registry.json"
    db_a = tmp_path / "a.db"
    _make_db(db_a)
    _write_registry(registry, [{"id": "ws_a", "db_path": str(db_a)}])
    monkeypatch.setattr(runner, "_registry_path", lambda: registry)
    rc = runner.main(["--json"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["workspace_id"] == "ws_a"
    assert parsed["status"] == "ok"


def test_main_unknown_workspace_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "registry.json"
    _write_registry(registry, [{"id": "ws_a", "db_path": str(tmp_path / "a.db")}])
    monkeypatch.setattr(runner, "_registry_path", lambda: registry)
    # ws_a is registered but its db file doesn't exist → _run_one returns
    # error but we filtered to ws_other which isn't in registry at all.
    rc = runner.main(["--workspace", "ws_other"])
    assert rc == 1


def test_main_no_registry_returns_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_registry_path", lambda: tmp_path / "missing.json")
    rc = runner.main([])
    assert rc == 0
