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
