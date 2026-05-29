from __future__ import annotations

import importlib.util
import sys
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


def test_run_json_records_elapsed_for_success() -> None:
    script = _load_script("memory_trust_dashboard.py")

    payload = script._run_json(
        "sample",
        [sys.executable, "-c", 'print(\'{"status":"ok","value":3}\')'],
        ok_codes={0},
        timeout_seconds=5.0,
    )

    assert payload["status"] == "ok"
    assert payload["value"] == 3
    assert payload["exit_code"] == 0
    assert payload["elapsed_sec"] >= 0


def test_run_json_times_out_and_reports_degraded() -> None:
    script = _load_script("memory_trust_dashboard.py")

    payload = script._run_json(
        "slow_check",
        [sys.executable, "-c", "import time; time.sleep(5)"],
        ok_codes={0},
        timeout_seconds=0.1,
    )

    assert payload["status"] == "degraded"
    assert payload["timed_out"] is True
    assert payload["exit_code"] is None
    assert payload["failures"] == ["slow_check timed out after 0.1s"]


def _dashboard_args(script: ModuleType, tmp_path: Path, *extra: str) -> object:
    return script._parser().parse_args(
        [
            "--workspace",
            "w",
            "--db-path",
            str(tmp_path / "m.db"),
            "--vector-path",
            str(tmp_path / "v.lance"),
            *extra,
        ]
    )


def test_run_dashboard_collects_all_components_in_spec_order(monkeypatch, tmp_path: Path) -> None:
    # Parallel run still collects every component, in deterministic spec order,
    # and still folds sentinel discovery into the watchdog component.
    script = _load_script("memory_trust_dashboard.py")
    seen: list[str] = []

    def fake_run_json(
        name: str, cmd: list[str], *, ok_codes: set[int], timeout_seconds: float
    ) -> dict[str, object]:
        seen.append(name)
        return {"status": "ok", "exit_code": 0}

    monkeypatch.setattr(script, "_run_json", fake_run_json)
    payload = script.run_dashboard(_dashboard_args(script, tmp_path))

    assert payload["status"] == "ok"
    names = list(payload["components"].keys())
    # First five are unconditional and ordered; watchdog always present.
    assert names[:5] == ["integrity", "workspace_doctor", "hygiene", "feedback", "encoding"]
    assert "watchdog" in names
    # `seen` records the script-basename label passed to _run_json (e.g.
    # "memory_audit"), not the component key, so compare counts.
    assert len(seen) == len(names)  # every component actually ran
    assert "memory_audit" in seen  # script label is used as the run label
    assert "sentinels_discovered" in payload["components"]["watchdog"]


def test_run_dashboard_marks_unspawnable_component_degraded(monkeypatch, tmp_path: Path) -> None:
    # A child that cannot even be spawned must degrade only itself, not abort
    # the whole dashboard.
    script = _load_script("memory_trust_dashboard.py")

    def fake_run_json(
        name: str, cmd: list[str], *, ok_codes: set[int], timeout_seconds: float
    ) -> dict[str, object]:
        # _run_json is called with the script-basename label, not the key.
        if name == "memory_hygiene":
            raise OSError("cannot spawn")
        return {"status": "ok", "exit_code": 0}

    monkeypatch.setattr(script, "_run_json", fake_run_json)
    payload = script.run_dashboard(_dashboard_args(script, tmp_path, "--skip-mcp"))

    assert payload["components"]["hygiene"]["status"] == "degraded"
    assert payload["status"] == "degraded"
    assert payload["components"]["integrity"]["status"] == "ok"


def test_run_dashboard_sequential_when_max_parallel_one(monkeypatch, tmp_path: Path) -> None:
    script = _load_script("memory_trust_dashboard.py")

    def fake_run_json(
        name: str, cmd: list[str], *, ok_codes: set[int], timeout_seconds: float
    ) -> dict[str, object]:
        return {"status": "ok", "exit_code": 0}

    monkeypatch.setattr(script, "_run_json", fake_run_json)
    payload = script.run_dashboard(_dashboard_args(script, tmp_path, "--max-parallel", "1"))
    assert payload["status"] == "ok"
    assert "integrity" in payload["components"]
