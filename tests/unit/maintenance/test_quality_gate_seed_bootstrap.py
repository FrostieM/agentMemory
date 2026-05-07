"""Regression test for the v1.2.4 quality_gate exception on seed_bootstrap.

The 1.2.3 seed adds project-AGNOSTIC discipline behavior_instructions
(``Link capability...``, ``Search before write...``) with
``source_type='seed_bootstrap'`` and no ``source_episode_id`` because
they are written by the seed orchestrator, not from a real episode.
Pre-1.2.4 this triggered ``behavior_instruction_without_source`` warnings
in quality_gate. The fix in ``maintenance/quality_gate_behavior.py``
adds ``seed_bootstrap`` to the authoritative-source allowlist alongside
``manual`` and ``system_seed``.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.maintenance.quality_gate_behavior import behavior_instruction_findings
from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)


def _seed_bi(workspace_id: str, name: str = "Some seed rule") -> BehaviorInstructionIn:
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name=name,
        rule="Apply this generic discipline rule on every workspace.",
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.USER_PREFERENCE,
        conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
        source_type="seed_bootstrap",
        # No source_episode_id — this is the regression path.
        confidence=0.9,
        active=True,
    )


def test_seed_bootstrap_bi_does_not_raise_without_source_warning(
    applied_conn: sqlite3.Connection,
) -> None:
    """1.2.4 lock: a behavior_instruction with source_type='seed_bootstrap'
    and no source_episode_id must NOT trigger
    ``behavior_instruction_without_source`` (it is authoritative — we
    wrote it ourselves via the seed orchestrator)."""
    upsert_behavior_instruction(applied_conn, _seed_bi("project-x"))
    findings = behavior_instruction_findings(applied_conn, workspace_id="project-x")
    kinds = {f.kind for f in findings}
    assert "behavior_instruction_without_source" not in kinds


def test_unknown_source_type_still_raises_without_source_warning(
    applied_conn: sqlite3.Connection,
) -> None:
    """Inverse: an unknown ``source_type`` (e.g. ``external_doc``,
    ``llm_extracted``) still raises the warning — only the curated
    authoritative set (``manual``, ``system_seed``, ``seed_bootstrap``)
    is exempt. Prevents the exception list from quietly silencing
    legitimate gaps."""
    bi_in = _seed_bi("project-x", name="Imported from doc")
    bi_in_ext = bi_in.model_copy(update={"source_type": "external_doc"})
    upsert_behavior_instruction(applied_conn, bi_in_ext)
    findings = behavior_instruction_findings(applied_conn, workspace_id="project-x")
    kinds = {f.kind for f in findings}
    assert "behavior_instruction_without_source" in kinds


def test_seed_bootstrap_not_treated_as_prompt_injection_risk(
    applied_conn: sqlite3.Connection,
) -> None:
    """Symmetric exception in the prompt-injection check: seed_bootstrap
    is in the trusted-source allowlist alongside manual / user_direct /
    system_seed. A seed BI containing imperative language ('always',
    'never', 'must') should NOT be flagged as injection risk because
    we wrote it ourselves."""
    bi = _seed_bi(
        "project-x",
        name="Imperative-sounding seed rule",
    )
    bi_imp = bi.model_copy(
        update={"rule": "Always run memory_search before any non-trivial write. Never skip."}
    )
    upsert_behavior_instruction(applied_conn, bi_imp)
    findings = behavior_instruction_findings(applied_conn, workspace_id="project-x")
    kinds = {f.kind for f in findings}
    assert "behavior_instruction_prompt_injection_risk" not in kinds
