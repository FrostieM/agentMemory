from __future__ import annotations

import argparse
import importlib.util
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


def test_quality_gate_cli_settings_honor_workspace_and_db_path(tmp_path: Path) -> None:
    script = _load_script("memory_quality_gate.py")
    db_path = tmp_path / "memory.db"

    settings = script._settings(argparse.Namespace(workspace="project", db_path=str(db_path)))

    assert settings.workspace_id == "project"
    assert settings.db_path == db_path
