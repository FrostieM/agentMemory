"""Behavior-instruction strict checks for the quality gate.

Split out of ``quality_gate_checks.py`` so each module stays under
the SLOC ceiling.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.maintenance.quality_gate_models import (
    QualityGateFinding,
    column_exists,
    is_expired,
    looks_like_prompt_injection,
    table_exists,
)


def behavior_instruction_findings(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[QualityGateFinding]:
    if not table_exists(conn, "behaviors"):
        return []
    has_governance = column_exists(conn, "behaviors", "source_type")
    governance_cols = (
        "source_type, source_id, reviewed_by, reviewed_at, expires_at, conflict_group"
        if has_governance
        else "'manual' AS source_type, NULL AS source_id, NULL AS reviewed_by, "
        "NULL AS reviewed_at, NULL AS expires_at, NULL AS conflict_group"
    )
    rows = conn.execute(
        f"""
        SELECT id, name, kind, priority, rule, source_episode_id, confidence,
               {governance_cols}
        FROM behaviors
        WHERE workspace_id = ? AND active = 1
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[QualityGateFinding] = []
    for row in rows:
        if float(row["confidence"]) < 0.5:
            findings.append(
                QualityGateFinding(
                    kind="low_confidence_behavior_instruction",
                    severity="warning",
                    target_type="behavior_instruction",
                    target_id=str(row["id"]),
                    summary="Active behavior instruction has low confidence.",
                    details={"name": row["name"], "confidence": row["confidence"]},
                )
            )
        source_type = str(row["source_type"] or "manual")
        source_id = row["source_id"]
        if (
            not row["source_episode_id"]
            and not source_id
            # 1.2.4: ``seed_bootstrap`` is authoritative — we wrote
            # the rule ourselves via setup_agent.py / project memory
            # seed. It legitimately has no source_episode_id and
            # should not raise the without-source warning. Same
            # status as ``system_seed`` and ``manual``.
            and source_type not in {"manual", "system_seed", "seed_bootstrap"}
        ):
            findings.append(
                QualityGateFinding(
                    kind="behavior_instruction_without_source",
                    severity="warning",
                    target_type="behavior_instruction",
                    target_id=str(row["id"]),
                    summary="Active behavior instruction has no source episode.",
                    details={
                        "name": row["name"],
                        "kind": row["kind"],
                        "priority": row["priority"],
                        "source_type": source_type,
                    },
                )
            )
        if is_expired(row["expires_at"]):
            findings.append(
                QualityGateFinding(
                    kind="expired_behavior_instruction_still_active",
                    severity="error",
                    target_type="behavior_instruction",
                    target_id=str(row["id"]),
                    summary="Expired behavior instruction is still active.",
                    details={"name": row["name"], "expires_at": row["expires_at"]},
                )
            )
        if source_type in {"external", "untrusted_doc"}:
            findings.append(
                QualityGateFinding(
                    kind="untrusted_behavior_instruction_active",
                    severity="error",
                    target_type="behavior_instruction",
                    target_id=str(row["id"]),
                    summary="Behavior instruction from untrusted content must not be active.",
                    details={"name": row["name"], "source_type": source_type},
                )
            )
        if source_type not in {
            "manual",
            "user_direct",
            "system_seed",
            "seed_bootstrap",  # 1.2.4: authoritative, same trust level
        } and looks_like_prompt_injection(str(row["rule"])):
            findings.append(
                QualityGateFinding(
                    kind="behavior_instruction_prompt_injection_risk",
                    severity="error",
                    target_type="behavior_instruction",
                    target_id=str(row["id"]),
                    summary="Behavior instruction contains prompt-injection language from a non-authoritative source.",
                    details={"name": row["name"], "source_type": source_type},
                )
            )
    return findings
