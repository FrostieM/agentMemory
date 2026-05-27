"""Crash-test orchestrator: setup → run all phases → teardown → report."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import httpx
from scripts.crash_test.http_service import ManagedHttpService
from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.phases.p00_setup import P00Setup
from scripts.crash_test.phases.p01_ingest import P01Ingest
from scripts.crash_test.phases.p02_retrieval import P02Retrieval
from scripts.crash_test.phases.p03_decisions import P03Decisions
from scripts.crash_test.phases.p04_theories import P04Theories
from scripts.crash_test.phases.p05_capabilities import P05Capabilities
from scripts.crash_test.phases.p06_behavior import P06Behavior
from scripts.crash_test.phases.p07_core_task import P07CoreTask
from scripts.crash_test.phases.p08_graph import P08Graph
from scripts.crash_test.phases.p09_operator import P09Operator
from scripts.crash_test.phases.p10_state_snapshots import P10StateSnapshots
from scripts.crash_test.phases.p11_review import P11Review
from scripts.crash_test.phases.p12_hygiene import P12Hygiene
from scripts.crash_test.phases.p13_compaction import P13Compaction
from scripts.crash_test.phases.p14_workspace import P14Workspace
from scripts.crash_test.phases.p15_quality_env import P15QualityEnv
from scripts.crash_test.phases.p16_v14_feedback import P16V14Feedback
from scripts.crash_test.phases.p17_v15_capability_maturity import P17V15CapabilityMaturity
from scripts.crash_test.phases.p18_v16_cold import P18V16Cold
from scripts.crash_test.phases.p19_v17_lineage_bridge import P19V17LineageBridge
from scripts.crash_test.phases.p20_v18_reflective import P20V18Reflective
from scripts.crash_test.phases.p21_v19_recurrence import P21V19Recurrence
from scripts.crash_test.phases.p22_trust_safety import P22TrustSafety
from scripts.crash_test.phases.p23_health_ui_html import P23HealthUiHtml
from scripts.crash_test.phases.p24_ui_browser import P24UiBrowser
from scripts.crash_test.phases.p25_v3_quality_loops import P25V3QualityLoops
from scripts.crash_test.phases.p26_v110_correction import P26V110Correction
from scripts.crash_test.reporter import Reporter
from scripts.crash_test.workspace import (
    QA_ROOT,
    bootstrap_db,
    ensure_clean_workspace,
    register_in_registry,
    remove_from_registry,
    teardown_workspace,
    workspace_paths,
)

PHASES: list[type[Phase]] = [
    P00Setup,
    P01Ingest,
    P02Retrieval,
    P03Decisions,
    P04Theories,
    P05Capabilities,
    P06Behavior,
    P07CoreTask,
    P08Graph,
    P09Operator,
    P10StateSnapshots,
    P11Review,
    P12Hygiene,
    P13Compaction,
    P14Workspace,
    P15QualityEnv,
    P16V14Feedback,
    P17V15CapabilityMaturity,
    P18V16Cold,
    P19V17LineageBridge,
    P20V18Reflective,
    P21V19Recurrence,
    P22TrustSafety,
    P25V3QualityLoops,
    P26V110Correction,
    P23HealthUiHtml,
    P24UiBrowser,
]


def _service_env(workspace_id: str, db_path: Path, vector_path: Path) -> dict[str, str]:
    """Env for the HTTP subprocess: route to test workspace + enable all
    v1.4-v1.9 evolution flags so phases 16-21 actually exercise them."""
    return {
        "MEMORY_WORKSPACE_ID": workspace_id,
        "MEMORY_DB_PATH": str(db_path),
        "VECTOR_DB_PATH": str(vector_path),
        "MEMORY_FORBID_DEFAULT_WORKSPACE": "false",
        "MEMORY_STRICT_WORKSPACE_ISOLATION": "false",
        "MEMORY_HUB_MODE": "false",
        "OLLAMA_PROBE_SKIP": "true",
        # v1.4
        "MEMORY_FEEDBACK_EWMA_ENABLED": "true",
        # v1.5
        "MEMORY_CAPABILITY_MATURITY_ENABLED": "true",
        "MEMORY_BEHAVIOR_APPLY_TRACKING_ENABLED": "true",
        # v1.6
        "MEMORY_COLD_TRACKING_ENABLED": "true",
        "MEMORY_COLD_AUTO_QUEUE_ENABLED": "true",
        # v1.7
        "MEMORY_THEORY_BRIDGE_ENABLED": "true",
        "MEMORY_THEORY_BRIDGE_MIN_EVIDENCE": "3",
        # v1.8
        "MEMORY_REFLECTIVE_COMPACT_ENABLED": "true",
        # v1.9
        "MEMORY_HYGIENE_PERSIST_ENABLED": "true",
        "MEMORY_SENTINEL_PERSIST_ENABLED": "true",
        # 1.1.0 improvements
        "MEMORY_IMPLICIT_FEEDBACK_ENABLED": "true",
        "MEMORY_SENTINEL_AUTORUN_HOURS": "0.0001",  # ~0.4s — always overdue in test
        "LOG_LEVEL": "WARNING",
    }


def run_crash_test(
    *,
    workspace_id: str = "qa-crash-test",
    port: int = 8766,
    artifacts_dir: Path | None = None,
    skip_ui: bool = False,
    skip_llm: bool = False,
) -> Reporter:
    artifacts_dir = artifacts_dir or (QA_ROOT.parent / "qa-crash-test-artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    reporter = Reporter()
    workspace_dir, db_path, vector_path = workspace_paths()

    # Always start from a clean slate.
    ensure_clean_workspace()
    bootstrap_db(workspace_id=workspace_id, db_path=db_path, vector_path=vector_path)
    register_in_registry(workspace_id=workspace_id, db_path=db_path, vector_path=vector_path)

    service = ManagedHttpService(
        port=port,
        env=_service_env(workspace_id, db_path, vector_path),
        log_path=artifacts_dir / "service.log",
    )
    state: CrashTestState | None = None
    try:
        service.start()
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        with httpx.Client(base_url=service.base_url, timeout=30.0) as client:
            state = CrashTestState(
                workspace_id=workspace_id,
                workspace_dir=workspace_dir,
                db_path=db_path,
                vector_path=vector_path,
                base_url=service.base_url,
                client=client,
                conn=conn,
                settings_env=_service_env(workspace_id, db_path, vector_path),
                artifacts_dir=artifacts_dir,
                skip_ui=skip_ui,
                skip_llm=skip_llm,
            )
            for phase_cls in PHASES:
                phase = phase_cls()
                started = time.monotonic()
                try:
                    phase_result = phase.run(state)
                except Exception as exc:
                    phase_result = PhaseResult(
                        name=phase.name,
                        description=phase.description,
                        status="error",
                    )
                    phase_result.problems.append(f"unhandled exception: {exc!r}")
                phase_result.duration_seconds = round(time.monotonic() - started, 3)
                if (
                    phase_result.passed
                    and phase_result.failed == 0
                    and phase_result.status == "pass"
                ):
                    phase_result.status = "pass"
                reporter.record(phase_result)
                if phase.fatal_on_failure and phase_result.status != "pass":
                    break
        conn.close()
    finally:
        service.stop()
        remove_from_registry(workspace_id=workspace_id)
        teardown_workspace()
    return reporter
