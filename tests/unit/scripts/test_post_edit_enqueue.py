"""Unit tests for scripts/post_edit_enqueue.py — PostToolUse hook.

Covers:

* ``_extract_file_path`` — accept Edit / Write / NotebookEdit shapes
* ``_resolve_workspace`` — walks file's parents against the registry
* End-to-end: feed an Edit event via captured stdin, assert the queue
  file received the right entry
* Failure-soft: malformed events / missing files / unregistered paths
  all exit 0 without enqueueing or raising
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts import post_edit_enqueue as hook

from agent_memory_lite.cognition import digest_worker as dw

# ============================================================
# _extract_file_path: accept each tool's payload shape
# ============================================================


@pytest.mark.parametrize(
    "event",
    [
        {"tool_name": "Edit", "tool_input": {"file_path": "/abs/x.py"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/abs/x.py"}},
        {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "/abs/x.py"}},
    ],
)
def test_extract_file_path_supported_tools(event: dict[str, Any]) -> None:
    assert hook._extract_file_path(event) == "/abs/x.py"


def test_extract_file_path_empty_payload() -> None:
    assert hook._extract_file_path({"tool_name": "Edit"}) == ""
    assert hook._extract_file_path({"tool_name": "Edit", "tool_input": {}}) == ""
    assert hook._extract_file_path({"tool_name": "Edit", "tool_input": {"file_path": ""}}) == ""


def test_extract_file_path_tolerates_non_dict_input() -> None:
    assert hook._extract_file_path({"tool_input": "not-a-dict"}) == ""


# ============================================================
# _resolve_workspace: walk parents against the registry
# ============================================================


def _write_registry(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({"version": 1, "workspaces": entries}), encoding="utf-8")


def test_resolve_workspace_direct_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "workspaces.json"
    project = tmp_path / "proj"
    project.mkdir()
    target = project / "src" / "foo.py"
    target.parent.mkdir(parents=True)
    target.write_text("# x", encoding="utf-8")
    _write_registry(
        registry,
        [
            {
                "id": "proj",
                "project_root": str(project),
                "db_path": str(tmp_path / "proj.db"),
                "vector_path": str(tmp_path / "proj.lance"),
            }
        ],
    )
    monkeypatch.setattr(hook, "REGISTRY_PATH", registry)
    ws, db = hook._resolve_workspace(target)
    assert ws == "proj"
    assert db.endswith("proj.db")


def test_resolve_workspace_no_registry_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "workspaces.json"
    _write_registry(registry, [])
    monkeypatch.setattr(hook, "REGISTRY_PATH", registry)
    target = tmp_path / "elsewhere.py"
    target.write_text("# x", encoding="utf-8")
    ws, db = hook._resolve_workspace(target)
    assert ws == ""
    assert db == ""


def test_resolve_workspace_rejects_unsafe_db_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "workspaces.json"
    project = tmp_path / "proj"
    project.mkdir()
    target = project / "src" / "foo.py"
    target.parent.mkdir(parents=True)
    target.write_text("# x", encoding="utf-8")
    _write_registry(
        registry,
        [
            {
                "id": "proj",
                "project_root": str(project),
                "db_path": "relative.db",
                "vector_path": "",
            }
        ],
    )
    monkeypatch.setattr(hook, "REGISTRY_PATH", registry)
    assert hook._resolve_workspace(target) == ("", "")


# ============================================================
# End-to-end main()
# ============================================================


def test_main_enqueues_when_workspace_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "workspaces.json"
    queue = tmp_path / "queue.jsonl"
    project = tmp_path / "proj"
    project.mkdir()
    target = project / "edited.py"
    target.write_text("def x(): pass", encoding="utf-8")
    _write_registry(
        registry,
        [
            {
                "id": "proj",
                "project_root": str(project),
                "db_path": str(tmp_path / "proj.db"),
                "vector_path": "",
            }
        ],
    )
    monkeypatch.setattr(hook, "REGISTRY_PATH", registry)
    # Redirect the digest queue path for this test.
    monkeypatch.setattr(dw, "DEFAULT_QUEUE_PATH", queue)

    event = {"tool_name": "Edit", "tool_input": {"file_path": str(target)}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    rc = hook.main()
    assert rc == 0
    assert queue.exists()
    line = queue.read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["workspace_id"] == "proj"
    assert payload["file_path"].endswith("edited.py")


def test_main_silent_when_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue.jsonl"
    monkeypatch.setattr(dw, "DEFAULT_QUEUE_PATH", queue)
    event = {"tool_name": "Edit", "tool_input": {"file_path": "/does/not/exist.py"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert hook.main() == 0
    assert not queue.exists()  # nothing enqueued


def test_main_silent_when_no_workspace_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "workspaces.json"
    _write_registry(registry, [])  # empty registry
    monkeypatch.setattr(hook, "REGISTRY_PATH", registry)
    queue = tmp_path / "queue.jsonl"
    monkeypatch.setattr(dw, "DEFAULT_QUEUE_PATH", queue)
    target = tmp_path / "orphan.py"
    target.write_text("# x", encoding="utf-8")
    event = {"tool_name": "Edit", "tool_input": {"file_path": str(target)}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert hook.main() == 0
    assert not queue.exists()


def test_main_tolerates_malformed_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue.jsonl"
    monkeypatch.setattr(dw, "DEFAULT_QUEUE_PATH", queue)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json at all"))
    assert hook.main() == 0
    assert not queue.exists()


def test_main_tolerates_empty_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue.jsonl"
    monkeypatch.setattr(dw, "DEFAULT_QUEUE_PATH", queue)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert hook.main() == 0
