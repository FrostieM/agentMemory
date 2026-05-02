"""CLI tests for `scripts/register_workspace.py`.

The script talks directly to `WorkspaceRegistry` (no HTTP). We invoke
the `main(argv)` entrypoint with a temp registry path so concurrent
test runs do not stomp each other.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "register_workspace_under_test", repo_root / "scripts" / "register_workspace.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def registry_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "workspaces.json"
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(path))
    return path


def test_register_creates_entry(
    registry_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    project = tmp_path / "alpha"
    project.mkdir()
    rc = cli.main(["register", "--workspace", "alpha", "--project", str(project)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "alpha"
    assert out["db_path"].endswith("memory.db")
    assert out["project_root"] == str(project)


def test_list_then_remove_round_trip(
    registry_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    project = tmp_path / "beta"
    project.mkdir()
    cli.main(["register", "--workspace", "beta", "--project", str(project)])
    capsys.readouterr()  # discard register output

    rc = cli.main(["list"])
    assert rc == 0
    listed = json.loads(capsys.readouterr().out)
    assert any(w["id"] == "beta" for w in listed["workspaces"])

    rc = cli.main(["remove", "--workspace", "beta"])
    assert rc == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["removed"] is True

    cli.main(["list"])
    listed_after = json.loads(capsys.readouterr().out)
    assert all(w["id"] != "beta" for w in listed_after["workspaces"])


def test_remove_unknown_returns_nonzero(
    registry_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    rc = cli.main(["remove", "--workspace", "ghost"])
    assert rc == 1
    body = json.loads(capsys.readouterr().out)
    assert body["removed"] is False


def test_register_requires_existing_project(
    registry_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    rc = cli.main(["register", "--workspace", "ghost", "--project", str(tmp_path / "nope")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "does not exist" in err


def test_register_idempotent_label_update(
    registry_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    project = tmp_path / "gamma"
    project.mkdir()
    cli.main(["register", "--workspace", "gamma", "--project", str(project), "--label", "first"])
    capsys.readouterr()
    cli.main(["register", "--workspace", "gamma", "--project", str(project), "--label", "second"])
    second = json.loads(capsys.readouterr().out)
    assert second["label"] == "second"

    cli.main(["list"])
    listed = json.loads(capsys.readouterr().out)
    gammas = [w for w in listed["workspaces"] if w["id"] == "gamma"]
    assert len(gammas) == 1
    assert gammas[0]["label"] == "second"
