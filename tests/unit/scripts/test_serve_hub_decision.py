"""Unit tests for `scripts/serve.py:decide_hub_mode`.

Precedence contract:
  CLI --hub  >  CLI --strict  >  MEMORY_HUB_MODE env  >  registry presence
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_serve() -> ModuleType:
    """Import once, return cached module so monkeypatched attributes persist."""
    cached = sys.modules.get("serve_under_test")
    if cached is not None:
        return cached
    repo_root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "serve_under_test", repo_root / "scripts" / "serve.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ns(*, hub: bool = False, strict: bool = False) -> argparse.Namespace:
    return argparse.Namespace(hub=hub, strict=strict)


@pytest.fixture
def empty_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    serve = _load_serve()
    registry_path = tmp_path / "empty_workspaces.json"
    monkeypatch.setattr(serve, "DEFAULT_REGISTRY", registry_path)
    monkeypatch.delenv("MEMORY_HUB_MODE", raising=False)
    return registry_path


@pytest.fixture
def populated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    serve = _load_serve()
    registry_path = tmp_path / "populated_workspaces.json"
    registry_path.write_text(
        '{"version": 1, "workspaces": [{"id": "alpha", "db_path": "/x"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(serve, "DEFAULT_REGISTRY", registry_path)
    monkeypatch.delenv("MEMORY_HUB_MODE", raising=False)
    return registry_path


def test_cli_hub_flag_wins_over_env_false(populated_registry: Path, monkeypatch) -> None:
    serve = _load_serve()
    monkeypatch.setenv("MEMORY_HUB_MODE", "false")
    assert serve.decide_hub_mode(_ns(hub=True)) is True


def test_cli_strict_flag_wins_over_env_true(populated_registry: Path, monkeypatch) -> None:
    serve = _load_serve()
    monkeypatch.setenv("MEMORY_HUB_MODE", "true")
    assert serve.decide_hub_mode(_ns(strict=True)) is False


def test_env_true_enables_hub(empty_registry: Path, monkeypatch) -> None:
    serve = _load_serve()
    monkeypatch.setenv("MEMORY_HUB_MODE", "true")
    assert serve.decide_hub_mode(_ns()) is True


def test_env_false_disables_hub_even_with_registry(populated_registry: Path, monkeypatch) -> None:
    serve = _load_serve()
    monkeypatch.setenv("MEMORY_HUB_MODE", "false")
    assert serve.decide_hub_mode(_ns()) is False


def test_registry_presence_default_enables_hub(populated_registry: Path) -> None:
    serve = _load_serve()
    assert serve.decide_hub_mode(_ns()) is True


def test_empty_registry_default_disables_hub(empty_registry: Path) -> None:
    serve = _load_serve()
    assert serve.decide_hub_mode(_ns()) is False


def test_registry_has_entries_handles_corrupted_file(tmp_path: Path) -> None:
    serve = _load_serve()
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    assert serve.registry_has_entries(bad) is False


def test_registry_has_entries_handles_missing_file(tmp_path: Path) -> None:
    serve = _load_serve()
    missing = tmp_path / "does_not_exist.json"
    assert serve.registry_has_entries(missing) is False
