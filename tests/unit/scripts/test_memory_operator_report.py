from __future__ import annotations

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


def test_operator_report_renders_dashboard_payload() -> None:
    script = _load_script("memory_operator_report.py")
    markdown = script.render_markdown(
        {
            "status": "ok",
            "workspace_id": "project-a",
            "db_path": ".agent_memory/memory.db",
            "vector_path": ".agent_memory/vectors.lance",
            "failures": [],
            "warnings": [],
            "components": {
                "integrity": {
                    "status": "ok",
                    "exit_code": 0,
                    "counts": {
                        "chunks": 7,
                        "chunks_fts": 7,
                        "vectors": 7,
                        "missing_embedding_ids": 0,
                        "open_maintenance_events": 0,
                        "hygiene_findings": 0,
                        "capability_links": 3,
                    },
                },
                "feedback": {
                    "status": "ok",
                    "exit_code": 0,
                    "counts": {"total": 2, "noisy_sources": 1},
                },
            },
        }
    )

    assert "# Memory Operator Report: project-a" in markdown
    assert "chunks / fts / vectors: `7` / `7` / `7`" in markdown
    assert "feedback total / noisy sources: `2` / `1`" in markdown
    assert "| integrity | ok | 0 |" in markdown
