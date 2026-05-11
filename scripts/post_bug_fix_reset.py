"""Reset counter baselines after the 6d2e085 hub-mode post-build fix.

The pre-fix code skipped maybe_track_behavior_application and
maybe_track_last_retrieved for any request whose workspace_id did not
match the service anchor — so counters in hub-mode-served workspaces
were unreliable: a rule could be firing every prompt with zero count,
and a chunk could be retrieved hundreds of times with last_retrieved_at
still NULL.

The fix shipped in 6d2e085 lands today. Counters now bump on every
served workspace. To get a clean 7-day measurement window, this script
resets every behavior_instruction.application_count + last_applied_at
to (0, NULL) and every chunks/decisions/theories.last_retrieved_at to
NULL across copyBot + agentLight. After 7 days of normal traffic the
operator can re-audit and trust the distribution.

Direct write via sqlite3 (the service is in hub mode, WAL journal
allows concurrent reads while we update). Each workspace gets its
own audit_log entry + a record_with_evidence decision capturing the
reset moment so the next agent sees it in <active_decisions>.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REGISTRY_PATH = Path.home() / ".agent_memory" / "workspaces.json"
SERVICE_BASE = "http://127.0.0.1:8765"
NOW_ISO = datetime.now(UTC).isoformat()

RESET_DECISION_TEXT = (
    "Reset baseline for behavior_instructions.application_count + "
    "last_applied_at + chunks/decisions/theories.last_retrieved_at on "
    "2026-05-11 after commit 6d2e085 fixed the hub-mode anchor guard in "
    "api/routes/context_post_build.py. Pre-fix the two post-build hooks "
    "maybe_track_behavior_application and maybe_track_last_retrieved bailed "
    "for any non-anchor workspace, so observed counters here are noise. "
    "Clean baseline + 7 days of normal traffic = trustworthy audit signal."
)

RESET_DECISION_RATIONALE = (
    "Without the reset, the next memory audit cannot distinguish 'rule was "
    "really dormant for 60 days' from 'rule fired every prompt but counter "
    "bug stamped nothing'. Zero-out makes the next 7-day window the source "
    "of truth. Idempotent if rerun, no destructive ops -- only counter "
    "fields, not row deletions or behavior_instruction inactivation."
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


def reset_workspace(workspace_id: str, db_path: Path) -> dict:
    """Direct-SQL reset of counter fields. WAL allows concurrent reads."""
    conn = sqlite3.connect(db_path)
    try:
        beh = conn.execute(
            "UPDATE behavior_instructions SET application_count=0, last_applied_at=NULL, "
            "updated_at=? WHERE workspace_id=? AND active=1",
            (NOW_ISO, workspace_id),
        ).rowcount
        chunks = conn.execute(
            "UPDATE chunks SET last_retrieved_at=NULL WHERE workspace_id=?",
            (workspace_id,),
        ).rowcount
        decisions = conn.execute(
            "UPDATE decisions SET last_retrieved_at=NULL WHERE workspace_id=?",
            (workspace_id,),
        ).rowcount
        theories = conn.execute(
            "UPDATE theories SET last_retrieved_at=NULL WHERE workspace_id=?",
            (workspace_id,),
        ).rowcount
        conn.commit()
        return {
            "behavior_instructions_reset": beh,
            "chunks_reset": chunks,
            "decisions_reset": decisions,
            "theories_reset": theories,
        }
    finally:
        conn.close()


def record_reset_decision(workspace_id: str, route: dict[str, str], counts: dict) -> dict:
    """Move 2 atomic compound: episode + decision + capability_link."""
    body = {
        "workspace_id": workspace_id,
        "evidence_text": (
            f"Counter reset 2026-05-11 after hub-mode fix 6d2e085. "
            f"Reset {counts['behavior_instructions_reset']} behavior_instructions, "
            f"{counts['chunks_reset']} chunks, {counts['decisions_reset']} decisions, "
            f"{counts['theories_reset']} theories. Next audit in 7 days."
        ),
        "evidence_trust_level": "agent_observed",
        "evidence_importance": 0.7,
        "decision_title": "Counter baseline reset after hub-mode fix",
        "decision_text": RESET_DECISION_TEXT,
        "decision_rationale": RESET_DECISION_RATIONALE,
        "decision_importance": 0.85,
        "decision_confidence": 0.95,
        "capability_type": "skill",
        "capability_name": "Memory population discipline",
        "capability_relation": "method",
        "capability_rationale": (
            "Memory hygiene reset is part of population discipline -- the "
            "counters are useless if we cannot trust them. This skill owns "
            "the discipline of recognising stale signals + reverting to a "
            "clean baseline rather than acting on bad data."
        ),
    }
    r = httpx.post(
        f"{SERVICE_BASE}/memory/record_with_evidence",
        headers=headers_for(route),
        json=body,
        timeout=60,
    )
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": r.text[:300]}
    return {"ok": True, **r.json()}


def main() -> int:
    registry = load_registry()
    print("=== Post-bug-fix counter reset ===\n")
    for ws in ["copyBot", "agentLight"]:
        route = registry.get(ws)
        if not route or not route.get("db_path"):
            print(f"  {ws}: SKIP -- not in registry")
            continue
        db = Path(route["db_path"])
        counts = reset_workspace(ws, db)
        print(f"  {ws}: {counts}")
        rec = record_reset_decision(ws, route, counts)
        if rec.get("ok"):
            print(f"    decision_id={rec.get('decision_id')}")
            print(f"    episode_id ={rec.get('episode_id')}")
        else:
            print(f"    record FAILED: {rec}")
    print("\nReset complete. Re-audit recommended on 2026-05-18 (7 days).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
