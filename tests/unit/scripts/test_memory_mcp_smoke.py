from __future__ import annotations

import importlib.util
from argparse import Namespace
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


def test_mcp_smoke_evaluate_requires_behavior_and_capabilities() -> None:
    script = _load_script("memory_mcp_smoke.py")
    status, failures, warnings = script._evaluate(
        {
            "status": "ok",
            "wall_elapsed_sec": 0.2,
            "has_brief": True,
            "has_behaviors": False,
            "has_agent_capabilities": False,
            "has_active_decisions": False,
            "tool_names": sorted(script.EXPECTED_V3_MCP_TOOLS),
        },
        Namespace(max_seconds=5.0, require_behavior=True, require_capabilities=True),
    )

    assert status == "degraded"
    assert "workspace has no active behaviors" in failures
    assert "workspace has no active capabilities" in failures
    assert warnings == ["workspace has no active decisions"]


def test_mcp_smoke_evaluate_passes_fast_context() -> None:
    script = _load_script("memory_mcp_smoke.py")
    status, failures, warnings = script._evaluate(
        {
            "status": "ok",
            "wall_elapsed_sec": 0.2,
            "has_brief": True,
            "has_behaviors": True,
            "has_agent_capabilities": True,
            "has_active_decisions": True,
            "tool_names": sorted(script.EXPECTED_V3_MCP_TOOLS),
        },
        Namespace(max_seconds=5.0, require_behavior=True, require_capabilities=True),
    )

    assert status == "ok"
    assert failures == []
    assert warnings == []
