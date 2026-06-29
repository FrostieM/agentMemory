"""Unit tests for the hub-mode workspace registry."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from agent_memory_lite.config.workspace_registry import WorkspaceRegistry


def test_register_creates_entry(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    entry = registry.register(
        workspace_id="agentLight",
        db_path="/projects/agent-memory-lite/.agent_memory/memory.db",
        vector_path="/projects/agent-memory-lite/.agent_memory/vectors.lance",
        label="agent-memory-lite",
        project_root="/projects/agent-memory-lite",
    )
    assert entry.id == "agentLight"
    assert entry.db_path.endswith("memory.db")
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["workspaces"][0]["id"] == "agentLight"


def test_register_updates_existing_entry(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    registry.register(
        workspace_id="agentLight",
        db_path="/old/memory.db",
        vector_path="/old/vectors.lance",
    )
    entry = registry.register(
        workspace_id="agentLight",
        db_path="/new/memory.db",
        vector_path="/new/vectors.lance",
        label="renamed",
    )
    assert entry.db_path == "/new/memory.db"
    assert entry.label == "renamed"
    assert len(registry.list()) == 1


def test_remove_returns_true_when_present(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    registry.register(
        workspace_id="agentLight",
        db_path="/projects/.agent_memory/memory.db",
        vector_path="/projects/.agent_memory/vectors.lance",
    )
    assert registry.remove("agentLight") is True
    assert registry.remove("agentLight") is False
    assert registry.list() == []


def test_list_handles_missing_or_corrupted_file(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    assert registry.list() == []
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text("not json", encoding="utf-8")
    assert registry.list() == []


def test_concurrent_writes_never_produce_a_torn_read(tmp_path: Path) -> None:
    """Batch C cert fix: registry writes are ATOMIC (temp file + os.replace), so a
    concurrent reader never observes a torn/truncated file as 'corrupt'. This is the
    regression the read guard exposed -- a torn parse used to set last_load_error and
    turn a transient write into a spurious workspace_unavailable refusal. Hammer
    register/remove on one thread while another repeatedly loads + checks
    last_load_error; it must stay None throughout.
    """
    reg_path = tmp_path / "workspaces.json"
    writer_reg = WorkspaceRegistry(reg_path)
    writer_reg.register(workspace_id="seed", db_path="/p/memory.db", vector_path="/p/v.lance")
    stop = threading.Event()
    torn: list[str] = []

    def writer() -> None:
        i = 0
        while not stop.is_set():
            writer_reg.register(
                workspace_id=f"ws{i % 5}", db_path=f"/p{i}/memory.db", vector_path=f"/p{i}/v.lance"
            )
            writer_reg.remove(f"ws{(i + 1) % 5}")
            i += 1

    def reader() -> None:
        for _ in range(3000):
            r = WorkspaceRegistry(reg_path)
            r.list()
            if r.last_load_error is not None:
                torn.append(r.last_load_error)

    wt = threading.Thread(target=writer)
    rt = threading.Thread(target=reader)
    wt.start()
    rt.start()
    rt.join()
    stop.set()
    wt.join()
    assert torn == [], f"reader observed torn/corrupt registry during concurrent writes: {torn[:5]}"
    # No stray temp files left behind by the atomic-replace dance.
    assert list(tmp_path.glob(".workspaces.json.*.tmp")) == []


def test_last_load_error_distinguishes_absent_from_corrupt(tmp_path: Path) -> None:
    """Batch C: an ABSENT registry is a clean empty (last_load_error None); a
    CORRUPT/unreadable one sets a typed marker so routing/anchoring callers can
    fail-closed with "registry unavailable" instead of silently treating it as
    "no workspaces registered" (which re-routes hub writes to the anchor DB)."""
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")

    # 1) Absent file -> clean empty, NO error.
    assert registry.list() == []
    assert registry.last_load_error is None

    # 2) Corrupt JSON -> empty + json marker.
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text("not json", encoding="utf-8")
    assert registry.list() == []
    assert registry.last_load_error == "corrupt:json"

    # 3) Valid JSON, wrong root shape (a list, not a dict) -> shape marker.
    registry.path.write_text("[]", encoding="utf-8")
    assert registry.list() == []
    assert registry.last_load_error == "corrupt:shape"

    # 4) A subsequent successful register/load clears the stale error.
    registry.register(
        workspace_id="agentLight",
        db_path="/p/.agent_memory/memory.db",
        vector_path="/p/.agent_memory/vectors.lance",
    )
    assert [e.id for e in registry.list()] == ["agentLight"]
    assert registry.last_load_error is None
