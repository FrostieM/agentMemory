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
memory_brief memory_search memory_get memory_write memory_edit memory_impact_check
memory_status memory_plan
scripts/memory_audit.py scripts/memory_quality_gate.py scripts/memory_mcp_smoke.py
scripts/memory_trust_dashboard.py
MEMORY_STRICT_WORKSPACE_ISOLATION
/ui /ui/recall /ui/metrics
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
memory_brief memory_search memory_get memory_write memory_edit memory_impact_check
memory_status memory_plan
scripts/memory_audit.py scripts/memory_quality_gate.py scripts/memory_mcp_smoke.py
scripts/memory_trust_dashboard.py
MEMORY_STRICT_WORKSPACE_ISOLATION
/ui /ui/recall /ui/metrics
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


def test_contract_check_rejects_active_legacy_surface_tokens(tmp_path: Path) -> None:
    script = _load_script("memory_contract_check.py")
    (tmp_path / "AGENTS.md").write_text(
        """
<!-- agent-memory-lite-contract:begin -->
Use workspace_id="<workspace_id>".
memory_brief memory_search memory_get memory_write memory_edit memory_impact_check
memory_status memory_plan
scripts/memory_audit.py scripts/memory_quality_gate.py scripts/memory_mcp_smoke.py
scripts/memory_trust_dashboard.py
MEMORY_STRICT_WORKSPACE_ISOLATION
/ui /ui/recall /ui/metrics
Before work, call memory_get_context and then /memory/list_decisions.
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

    assert payload["status"] == "degraded"
    assert {item["kind"] for item in payload["findings"]} == {
        "legacy_http_route",
        "legacy_mcp_tool",
    }


def test_contract_check_allows_removed_legacy_references(tmp_path: Path) -> None:
    script = _load_script("memory_contract_check.py")
    (tmp_path / "AGENTS.md").write_text(
        """
<!-- agent-memory-lite-contract:begin -->
Use workspace_id="<workspace_id>".
memory_brief memory_search memory_get memory_write memory_edit memory_impact_check
memory_status memory_plan
scripts/memory_audit.py scripts/memory_quality_gate.py scripts/memory_mcp_smoke.py
scripts/memory_trust_dashboard.py
MEMORY_STRICT_WORKSPACE_ISOLATION
/ui /ui/recall /ui/metrics
memory_get_context was removed.
/memory/list_decisions is not active.
decision_candidates were removed.
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
memory_brief memory_search memory_get memory_write memory_edit memory_impact_check
memory_status memory_plan
scripts/memory_audit.py scripts/memory_quality_gate.py scripts/memory_mcp_smoke.py
scripts/memory_trust_dashboard.py
MEMORY_STRICT_WORKSPACE_ISOLATION
/ui /ui/recall /ui/metrics
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


def test_contract_check_default_scan_skips_historical_adr_docs(tmp_path: Path) -> None:
    script = _load_script("memory_contract_check.py")
    (tmp_path / "AGENTS.md").write_text(
        """
<!-- agent-memory-lite-contract:begin -->
Use workspace_id="<workspace_id>".
memory_brief memory_search memory_get memory_write memory_edit memory_impact_check
memory_status memory_plan
scripts/memory_audit.py scripts/memory_quality_gate.py scripts/memory_mcp_smoke.py
scripts/memory_trust_dashboard.py
MEMORY_STRICT_WORKSPACE_ISOLATION
/ui /ui/recall /ui/metrics
<!-- agent-memory-lite-contract:end -->
""",
        encoding="utf-8",
    )
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-example.md").write_text(
        'Historical example workspace_id="default" and copyBot.',
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
