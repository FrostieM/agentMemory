"""Integration: ingest_file end-to-end with idempotent re-ingest."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.ingestion.file_pipeline import ingest_file
from agent_memory_lite.repositories.files_repo import get_file_by_path

pytestmark = pytest.mark.integration


PY_SOURCE = '''\
"""sample"""


def reindex_chunks():
    return True


class Helper:
    def collect(self):
        return None
'''


def test_first_ingest_creates_chunks(applied_conn: sqlite3.Connection) -> None:
    result = ingest_file(
        applied_conn,
        workspace_id="default",
        path="src/sample.py",
        content=PY_SOURCE,
        language="python",
    )
    assert not result.skipped
    assert result.chunks_written >= 2

    hits = search_chunks_fts(applied_conn, workspace_id="default", query="reindex_chunks", limit=10)
    assert any(hit.path == "src/sample.py" for hit in hits)


def test_re_ingest_unchanged_skips(applied_conn: sqlite3.Connection) -> None:
    ingest_file(
        applied_conn,
        workspace_id="default",
        path="src/sample.py",
        content=PY_SOURCE,
        language="python",
    )
    second = ingest_file(
        applied_conn,
        workspace_id="default",
        path="src/sample.py",
        content=PY_SOURCE,
        language="python",
    )
    assert second.skipped is True
    assert second.chunks_written == 0


def test_re_ingest_changed_replaces_chunks(applied_conn: sqlite3.Connection) -> None:
    ingest_file(
        applied_conn,
        workspace_id="default",
        path="src/sample.py",
        content=PY_SOURCE,
        language="python",
    )
    new_source = PY_SOURCE.replace("reindex_chunks", "rebuild_index")
    second = ingest_file(
        applied_conn,
        workspace_id="default",
        path="src/sample.py",
        content=new_source,
        language="python",
    )
    assert not second.skipped
    file_record = get_file_by_path(applied_conn, workspace_id="default", path="src/sample.py")
    assert file_record is not None
    assert file_record.content_hash != ""

    old_hits = search_chunks_fts(
        applied_conn, workspace_id="default", query="reindex_chunks", limit=10
    )
    new_hits = search_chunks_fts(
        applied_conn, workspace_id="default", query="rebuild_index", limit=10
    )
    assert old_hits == []
    assert any(hit.path == "src/sample.py" for hit in new_hits)


def test_markdown_file_chunks_by_heading(applied_conn: sqlite3.Connection) -> None:
    md = "# Title\nintro\n\n## Section\nbody\n"
    result = ingest_file(
        applied_conn,
        workspace_id="default",
        path="docs/readme.md",
        content=md,
        language="markdown",
    )
    assert result.chunks_written == 2
