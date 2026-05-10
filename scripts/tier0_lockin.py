"""Tier 0 lock-in: snapshots + memory-side decisions + new playbook.

Locks the post-cleanup state into both workspaces' memory:
- ``memory_snapshot_save`` -> diff baseline for future operators.
- ``memory_record_with_evidence`` (Move 2) -> atomic episode + decision +
  capability_link declaring the cleanup outcome. Surfaces in
  ``<active_decisions>`` of every future envelope so the next agent
  doesn't re-run the same triage.
- ``memory_upsert_agent_playbook`` -> codifies the Tier 0 procedure
  for the agentLight workspace (copyBot already has ``weekly_memory_hygiene``).

Routes via X-Memory-DB-Path so each call lands on the right physical DB.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

REGISTRY_PATH = Path.home() / ".agent_memory" / "workspaces.json"
SERVICE_BASE = "http://127.0.0.1:8765"

EPISODE_TEXT_AGENTLIGHT = (
    "Tier 0 memory hygiene cleanup completed 2026-05-10. Hygiene findings 23 -> 1 "
    "(96% reduction). Applied: 12 capability_links via auto_triage --apply --backup-first; "
    "archived 3 ephemeral smoke-test theories (th_38c83da5fa9e2455, th_b95f78bf3eeb66ae, "
    "th_85de8ad168e06e2d); linked 7 unlinked insights to best-fit existing decisions and "
    "the Memory-population-discipline skill. Full operational scripts at "
    "scripts/tier0_baseline.py + tier0_manual_list.py + tier0_apply.py. Backup at "
    ".agent_memory/backups/memory_before_auto_triage_20260510T134456Z.db."
)

EPISODE_TEXT_COPYBOT = (
    "Tier 0 memory hygiene cleanup applied 2026-05-10. Hygiene findings 10 -> 6 "
    "(40% reduction; remaining are 2 live theories awaiting evidence and 4 manual-review "
    "capability_links). Applied: 5 capability_links via auto_triage --apply --backup-first; "
    "rejected 9 noise candidates (kind=bug/fix/task_state, all <7 days old, none promotable "
    "per server validation). Full operational scripts in agent-memory-lite repo at "
    "scripts/tier0_*.py. Backup at "
    ".agent_memory/backups/memory_before_auto_triage_20260510T134439Z.db."
)


def load_registry() -> dict[str, dict[str, str]]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {
        w["id"]: {"db_path": w.get("db_path", ""), "vector_path": w.get("vector_path", "")}
        for w in payload.get("workspaces", [])
    }


def headers_for(route: dict[str, str]) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if route.get("db_path"):
        h["X-Memory-DB-Path"] = route["db_path"]
    if route.get("vector_path"):
        h["X-Memory-Vector-Path"] = route["vector_path"]
    return h


def snapshot_save(workspace_id: str, route: dict[str, str], name: str) -> dict:
    r = httpx.post(
        f"{SERVICE_BASE}/memory/snapshot_save",
        headers=headers_for(route),
        json={"workspace_id": workspace_id, "name": name, "metadata": {"phase": "tier0"}},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def record_with_evidence(workspace_id: str, route: dict[str, str], payload: dict) -> dict:
    r = httpx.post(
        f"{SERVICE_BASE}/memory/record_with_evidence",
        headers=headers_for(route),
        json=payload,
        timeout=60,
    )
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": r.text[:300]}
    return {"ok": True, **r.json()}


def upsert_playbook(workspace_id: str, route: dict[str, str], payload: dict) -> dict:
    r = httpx.post(
        f"{SERVICE_BASE}/memory/upsert_agent_playbook",
        headers=headers_for(route),
        json=payload,
        timeout=30,
    )
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": r.text[:300]}
    return {"ok": True, **r.json()}


def main() -> int:
    registry = load_registry()
    al = registry["agentLight"]
    cb = registry["copyBot"]

    print("=== Tier 0 lock-in ===\n")

    # 1. Snapshots
    print("--- Snapshots ---")
    snap_al = snapshot_save("agentLight", al, "post-tier0-cleanup-2026-05-10")
    print(f"  agentLight: snapshot_id={snap_al.get('snapshot_id', '?')}")
    snap_cb = snapshot_save("copyBot", cb, "post-tier0-cleanup-2026-05-10")
    print(f"  copyBot   : snapshot_id={snap_cb.get('snapshot_id', '?')}")
    print()

    # 2. record_with_evidence (Move 2 atomic compound)
    print("--- record_with_evidence (decision + episode + capability_link) ---")
    al_payload = {
        "workspace_id": "agentLight",
        "evidence_text": EPISODE_TEXT_AGENTLIGHT,
        "evidence_trust_level": "agent_observed",
        "evidence_importance": 0.7,
        "decision_title": "Tier 0 memory hygiene cleanup completed",
        "decision_text": (
            "Applied tier 0 memory cleanup on 2026-05-10: auto-triage capability_links "
            "(12 applied), archive 3 ephemeral smoke theories, link 7 unlinked insights "
            "to existing decisions/skills. Hygiene findings dropped from 23 to 1 "
            "(96% reduction). Operational scripts at scripts/tier0_*.py are idempotent "
            "and ready for quarterly re-runs."
        ),
        "decision_rationale": (
            "Operator-driven Tier 0 cleanup in response to noise-spiral concern raised "
            "in another agent's evaluation. Data showed historical 86% rejection rate, "
            "so noise was operator-discipline-managed rather than runaway -- focus shifted "
            "to closing hygiene findings (capability_links, theories, insights) instead."
        ),
        "decision_importance": 0.85,
        "decision_confidence": 0.95,
        "capability_type": "skill",
        "capability_name": "Memory population discipline",
        "capability_relation": "method",
        "capability_rationale": (
            "Tier 0 cleanup IS an instance of memory-population-discipline applied "
            "retroactively. The skill owns the operational space for write-time + "
            "audit-time discipline."
        ),
    }
    al_out = record_with_evidence("agentLight", al, al_payload)
    print(f"  agentLight: {al_out}")

    cb_payload = {
        "workspace_id": "copyBot",
        "evidence_text": EPISODE_TEXT_COPYBOT,
        "evidence_trust_level": "agent_observed",
        "evidence_importance": 0.7,
        "decision_title": "Tier 0 memory hygiene cleanup applied to copyBot",
        "decision_text": (
            "Applied tier 0 memory cleanup on 2026-05-10: auto-triage capability_links "
            "(5 applied), reject 9 noise candidates (kind=bug/fix/task_state, none "
            "promotable). Hygiene findings dropped from 10 to 6 (residual: 2 live "
            "theories awaiting evidence + 4 manual-review capability_links). Operational "
            "scripts at agent-memory-lite/scripts/tier0_*.py are idempotent."
        ),
        "decision_rationale": (
            "Operator-driven cleanup. Historical 86% rejection rate (83/97) shows "
            "discipline already compensating for noisy heuristic extractor; focus is "
            "closing hygiene debt rather than chasing extraction quality."
        ),
        "decision_importance": 0.85,
        "decision_confidence": 0.95,
        "capability_type": "playbook",
        "capability_name": "weekly_memory_hygiene",
        "capability_relation": "validation_playbook",
        "capability_rationale": (
            "Tier 0 cleanup IS the weekly_memory_hygiene playbook applied at quarterly "
            "scale. Capability link makes the connection explicit so future runs of the "
            "playbook reference this decision as the canonical 2026-05-10 baseline."
        ),
    }
    cb_out = record_with_evidence("copyBot", cb, cb_payload)
    print(f"  copyBot   : {cb_out}")
    print()

    # 3. New playbook on agentLight (copyBot already has weekly_memory_hygiene)
    print("--- agentLight playbook: tier0_quarterly_hygiene ---")
    pb_payload = {
        "workspace_id": "agentLight",
        "name": "tier0_quarterly_hygiene",
        "goal": (
            "Quarterly memory hygiene sweep: auto-triage capability_links, "
            "review pending candidates, archive zombie theories, link unlinked insights, "
            "snapshot the post-cleanup state for future diff baselines."
        ),
        "triggers": [
            "Scheduled every 90 days on agentLight",
            "Hygiene findings count exceeds 30",
            "Operator notices envelope clutter from old decisions",
            "Operator rejects >50% of pending candidates over a 2-week window",
        ],
        "steps": [
            "1. Run scripts/tier0_baseline.py to capture metrics + classify candidates.",
            "2. Run scripts/memory_auto_triage.py --apply --backup-first per workspace.",
            "3. Run scripts/tier0_manual_list.py to enumerate remaining manual items.",
            "4. Operator approves bulk reject + archive + link batches.",
            "5. Run scripts/tier0_apply.py to execute approved batches.",
            "6. Run scripts/tier0_lockin.py to snapshot + record decision + episode.",
            "7. Verify: ruff + mypy + check_sloc + 971 pytest + 27-phase crash test.",
            "8. Push if all gates green and operator approves.",
        ],
        "success_criteria": [
            "Hygiene findings below 10",
            "Pending candidates queue below 10 per workspace",
            "Backup files exist in <workspace>/.agent_memory/backups/",
            "memory_snapshot_save record exists with name post-tier0-cleanup-<date>",
            "Decision 'Tier 0 memory hygiene cleanup' present in active_decisions",
        ],
        "required_skills": ["Memory population discipline"],
        "confidence": 0.85,
    }
    pb_out = upsert_playbook("agentLight", al, pb_payload)
    print(f"  {pb_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
