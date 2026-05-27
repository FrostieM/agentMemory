"""Unit tests for the v3 digest worker pipeline.

Covers:

* ``enqueue`` / ``_read_queue`` / ``_dedupe_entries`` queue mechanics
* ``compute_digest`` heuristic for Python + non-Python files
* ``upsert_digest`` SQL roundtrip against a real v3-schema DB
* ``process_entry`` end-to-end with a temp file + temp DB
* ``drain_queue`` integration: queue → process → upsert → clear
* ``run_worker`` bounded loop
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_memory_lite.cognition import digest_worker as dw


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh v3-schema SQLite DB on disk so the worker can connect by path."""
    path = tmp_path / "canonical.db"
    conn = sqlite3.connect(path)
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(conn)
    conn.commit()
    conn.close()
    return path


# ============================================================
# Queue mechanics
# ============================================================


def test_enqueue_and_read_roundtrip(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.jsonl"
    dw.enqueue(
        workspace_id="ws",
        db_path=str(tmp_path / "x.db"),
        file_path="/a/b.py",
        project_root="/a",
        queue_path=qpath,
    )
    dw.enqueue(
        workspace_id="ws", db_path=str(tmp_path / "x.db"), file_path="/a/c.py", queue_path=qpath
    )
    entries = dw._read_queue(qpath)
    assert len(entries) == 2
    assert {e.file_path for e in entries} == {"/a/b.py", "/a/c.py"}
    assert {e.project_root for e in entries} == {"/a", ""}


def test_read_queue_skips_malformed_lines(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.jsonl"
    qpath.write_text(
        '{"workspace_id":"ws","db_path":"x.db","file_path":"/a","ts":1}\n'
        "this is not json\n"
        '{"workspace_id":"","db_path":"x","file_path":"/b","ts":2}\n'  # invalid: empty ws
        '{"workspace_id":"ws","db_path":"x.db","file_path":"/c","ts":3}\n',
        encoding="utf-8",
    )
    entries = dw._read_queue(qpath)
    assert {e.file_path for e in entries} == {"/a", "/c"}


def test_read_queue_caps_to_max_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dw, "MAX_PENDING", 3)
    qpath = tmp_path / "queue.jsonl"
    lines = [
        json.dumps({"workspace_id": "ws", "db_path": "x.db", "file_path": f"/f{i}", "ts": i})
        for i in range(10)
    ]
    qpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    entries = dw._read_queue(qpath)
    assert len(entries) == 3
    # Cap retains the *newest* (highest-ts) entries.
    assert {e.file_path for e in entries} == {"/f7", "/f8", "/f9"}


def test_dedupe_keeps_newest_per_file() -> None:
    entries = [
        dw.QueueEntry(workspace_id="ws", db_path="x", file_path="/a", ts=1.0),
        dw.QueueEntry(workspace_id="ws", db_path="x", file_path="/a", ts=3.0),
        dw.QueueEntry(workspace_id="ws", db_path="x", file_path="/a", ts=2.0),
        dw.QueueEntry(workspace_id="ws", db_path="x", file_path="/b", ts=2.0),
    ]
    deduped = dw._dedupe_entries(entries)
    by_file = {e.file_path: e.ts for e in deduped}
    assert by_file == {"/a": 3.0, "/b": 2.0}


def test_clear_queue_truncates_file(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.jsonl"
    qpath.write_text("garbage\n", encoding="utf-8")
    dw._clear_queue(qpath)
    assert qpath.read_text(encoding="utf-8") == ""


# ============================================================
# Per-file digest heuristic
# ============================================================


def test_python_purpose_short_extracts_docstring_first_line() -> None:
    source = '"""First sentence here.\n\nSecond paragraph."""\n\ndef foo(): pass'
    assert dw._python_purpose_short(source) == "First sentence here."


def test_python_purpose_short_handles_no_docstring() -> None:
    assert dw._python_purpose_short("def foo(): pass") == ""


def test_python_purpose_short_handles_syntax_error() -> None:
    assert dw._python_purpose_short("def {{{ broken") == ""


def test_python_top_symbols_returns_function_and_class_names() -> None:
    source = '"""m."""\n\ndef foo(): pass\nclass Bar: pass\nasync def baz(): pass'
    syms = dw._python_top_symbols(source)
    names = [s["name"] for s in syms]
    assert names == ["foo", "Bar", "baz"]
    assert syms[1]["kind"] == "class"


def test_generic_top_symbols_picks_declarations() -> None:
    source = (
        "// comment\n"
        "function alpha() {}\n"
        "class Beta {}\n"
        "def gamma(): pass\n"
        "## header\n"
        "ignored line that's not a decl\n"
    )
    syms = dw._generic_top_symbols(source)
    assert len(syms) == 4
    assert any("alpha" in s["name"] for s in syms)
    assert any("header" in s["name"] for s in syms)


def test_compute_digest_python_file() -> None:
    source = '"""Purpose line."""\n\ndef foo(): pass\nclass Bar: pass\n'
    result = dw.compute_digest("/abs/path/foo.py", source)
    assert result.language == "python"
    assert result.purpose_short == "Purpose line."
    assert {s["name"] for s in result.top_symbols} == {"foo", "Bar"}
    assert result.symbol_count == 2
    assert result.file_sha1  # 40-hex sha1


def test_compute_digest_non_python_file() -> None:
    source = "# Title\n\nSome content\n"
    result = dw.compute_digest("/abs/path/readme.md", source)
    assert result.language == "markdown"
    assert result.purpose_short == "# Title"


def test_compute_digest_unknown_extension() -> None:
    result = dw.compute_digest("/abs/path/data.xyz", "first line\nsecond\n")
    assert result.language is None
    assert result.purpose_short == "first line"


# ============================================================
# SQL upsert
# ============================================================


def test_upsert_digest_inserts_new_row(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = dw.compute_digest("/repo/foo.py", '"""Purpose."""\ndef x(): pass\n')
        dw.upsert_digest(conn, workspace_id="default", result=result)
        row = conn.execute("SELECT file_path, purpose_short, language FROM code_digests").fetchone()
        assert row["file_path"] == "/repo/foo.py"
        assert row["purpose_short"] == "Purpose."
        assert row["language"] == "python"
    finally:
        conn.close()


def test_upsert_digest_replaces_existing_row(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        first = dw.compute_digest("/repo/foo.py", '"""V1."""\ndef x(): pass\n')
        dw.upsert_digest(conn, workspace_id="default", result=first)
        second = dw.compute_digest("/repo/foo.py", '"""V2."""\ndef x(): pass\n')
        dw.upsert_digest(conn, workspace_id="default", result=second)
        count = conn.execute("SELECT COUNT(*) FROM code_digests").fetchone()[0]
        assert count == 1
        purpose = conn.execute("SELECT purpose_short FROM code_digests").fetchone()[0]
        assert purpose == "V2."
    finally:
        conn.close()


# ============================================================
# End-to-end: process_entry + drain_queue
# ============================================================


def test_process_entry_writes_digest_row(db_path: Path, tmp_path: Path) -> None:
    src = tmp_path / "foo.py"
    src.write_text('"""Hello world."""\ndef bar(): pass\n', encoding="utf-8")
    entry = dw.QueueEntry(
        workspace_id="default",
        db_path=str(db_path),
        file_path=str(src),
        ts=1.0,
    )
    digest_id = dw.process_entry(entry)
    assert digest_id is not None
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT id, purpose_short FROM code_digests").fetchone()
        assert row[0] == digest_id
        assert row[1] == "Hello world."
    finally:
        conn.close()


def test_process_entry_uses_relative_digest_key_when_project_root_present(
    db_path: Path, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    src = project / "scripts" / "hook.py"
    src.parent.mkdir(parents=True)
    src.write_text('"""Hook."""\ndef run(): pass\n', encoding="utf-8")
    entry = dw.QueueEntry(
        workspace_id="default",
        db_path=str(db_path),
        file_path=str(src),
        project_root=str(project),
        ts=1.0,
    )
    digest_id = dw.process_entry(entry)
    assert digest_id is not None
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT file_path, purpose_short FROM code_digests").fetchall()
        assert rows == [("scripts/hook.py", "Hook.")]
    finally:
        conn.close()


def test_process_entry_skips_missing_file(db_path: Path) -> None:
    entry = dw.QueueEntry(
        workspace_id="default",
        db_path=str(db_path),
        file_path="/does/not/exist.py",
        ts=1.0,
    )
    assert dw.process_entry(entry) is None


def test_drain_queue_full_cycle(db_path: Path, tmp_path: Path) -> None:
    qpath = tmp_path / "queue.jsonl"
    src_a = tmp_path / "a.py"
    src_a.write_text('"""A."""\n', encoding="utf-8")
    src_b = tmp_path / "b.py"
    src_b.write_text('"""B."""\n', encoding="utf-8")
    dw.enqueue(workspace_id="default", db_path=str(db_path), file_path=str(src_a), queue_path=qpath)
    dw.enqueue(workspace_id="default", db_path=str(db_path), file_path=str(src_b), queue_path=qpath)
    processed = dw.drain_queue(qpath)
    assert processed == 2
    # Queue file emptied.
    assert qpath.read_text(encoding="utf-8") == ""
    # Both digests persisted.
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM code_digests").fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_drain_queue_no_op_on_empty_queue(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.jsonl"
    assert dw.drain_queue(qpath) == 0


def test_run_worker_bounded_iterations(
    tmp_path: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qpath = tmp_path / "queue.jsonl"
    src = tmp_path / "x.py"
    src.write_text('"""x."""\n', encoding="utf-8")
    dw.enqueue(workspace_id="default", db_path=str(db_path), file_path=str(src), queue_path=qpath)
    # Patch time.sleep to a no-op so bounded run completes immediately.
    monkeypatch.setattr(dw.time, "sleep", lambda _: None)
    total = dw.run_worker(queue_path=qpath, poll_interval_s=0.01, max_iterations=2)
    assert total == 1  # one digest produced across iterations
