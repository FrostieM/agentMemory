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


def test_contract_check_detects_hard_coded_default_workspace(tmp_path: Path) -> None:
    script = _load_script("memory_contract_check.py")
    (tmp_path / "AGENTS.md").write_text(
        """
<!-- agent-memory-lite-contract:begin -->
memory_get_context memory_search memory_ingest_episode memory_write_theory
memory_upsert_behavior_instruction memory_list_agent_capabilities memory_link_capability
scripts/memory_audit.py scripts/memory_hygiene.py scripts/memory_watchdog.py
scripts/memory_quality_gate.py scripts/memory_encoding_audit.py
scripts/memory_workspace_doctor.py scripts/memory_feedback_report.py
scripts/memory_operator_report.py
scripts/memory_service_task.ps1 scripts/memory_trend_report.py
MEMORY_STRICT_WORKSPACE_ISOLATION
/ui /memory/ui/state
/memory/explain_context /memory/record_usage_feedback
{"workspace_id":"default"}
<!-- agent-memory-lite-contract:end -->
""",
        encoding="utf-8",
    )

    payload = script.run_contract_check(
        root=tmp_path,
        workspace_id="project",
        explicit_paths=[],
        allowed_project_names=set(),
    )

    assert payload["status"] == "warning"
    assert payload["counts"]["findings"] == 1
    assert payload["findings"][0]["kind"] == "hard_coded_default_workspace"


def test_contract_check_accepts_generic_contract(tmp_path: Path) -> None:
    script = _load_script("memory_contract_check.py")
    (tmp_path / "AGENTS.md").write_text(
        """
<!-- agent-memory-lite-contract:begin -->
Use workspace_id="<workspace_id>".
memory_get_context memory_search memory_ingest_episode memory_write_theory
memory_upsert_behavior_instruction memory_list_agent_capabilities memory_link_capability
scripts/memory_audit.py scripts/memory_hygiene.py scripts/memory_watchdog.py
scripts/memory_quality_gate.py scripts/memory_encoding_audit.py
scripts/memory_workspace_doctor.py scripts/memory_feedback_report.py
scripts/memory_operator_report.py
scripts/memory_service_task.ps1 scripts/memory_trend_report.py
MEMORY_STRICT_WORKSPACE_ISOLATION
/ui /memory/ui/state
/memory/explain_context /memory/record_usage_feedback
<!-- agent-memory-lite-contract:end -->
""",
        encoding="utf-8",
    )

    payload = script.run_contract_check(
        root=tmp_path,
        workspace_id="project",
        explicit_paths=[],
        allowed_project_names=set(),
    )

    assert payload["status"] == "ok"
    assert payload["findings"] == []


def test_contract_check_allows_project_specific_workspace_correction(
    tmp_path: Path,
) -> None:
    script = _load_script("memory_contract_check.py")
    (tmp_path / "AGENTS.md").write_text(
        """
<!-- agent-memory-lite-contract:begin -->
This project is copyBot, not default.
Use workspace_id="copyBot"; fix old rows with workspace_id='default' only by
migrating them back to copyBot.
memory_get_context memory_search memory_ingest_episode memory_write_theory
memory_upsert_behavior_instruction memory_list_agent_capabilities memory_link_capability
scripts/memory_audit.py scripts/memory_hygiene.py scripts/memory_watchdog.py
scripts/memory_quality_gate.py scripts/memory_encoding_audit.py
scripts/memory_workspace_doctor.py scripts/memory_feedback_report.py
scripts/memory_operator_report.py
scripts/memory_service_task.ps1 scripts/memory_trend_report.py
MEMORY_STRICT_WORKSPACE_ISOLATION
/ui /memory/ui/state
/memory/explain_context /memory/record_usage_feedback
<!-- agent-memory-lite-contract:end -->
""",
        encoding="utf-8",
    )

    payload = script.run_contract_check(
        root=tmp_path,
        workspace_id="copyBot",
        explicit_paths=[],
        allowed_project_names={"copyBot"},
    )

    assert payload["status"] == "ok"
    assert payload["findings"] == []
