"""Seed canonical discipline rules — the durable layer of the agent's habits.

The canonical architecture has three layers that funnel the agent toward
graph tools instead of Read/Grep:

  1. memory_impact_check primitive — makes the right thing cheap.
  2. memory_lint discipline_hint — makes the right thing obvious
     at the point of decision (PreToolUse advisory).
  3. Brief identity discipline line — makes the rule ambient
     (every session sees it).
  4. **Pinned behavior_instruction (this script)** — makes the rule
     DURABLE. A pinned row in the behaviors table rides every
     brief regardless of recency / relevance, so the rule survives
     workspace history rewrites, context-clearing, and operator
     opt-outs of brief identity tweaks.

Idempotent: re-running the script is a no-op for already-seeded rules.
Identified by ``name`` per kind+workspace.

Usage::

    python scripts/seed_memory_discipline.py --workspace <id> --db-path <path>
    python scripts/seed_memory_discipline.py --workspace <id> --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Each rule is `pinned=1` + `active=1`, so it rides every brief
# behaviors section without competing on relevance score. Keep the
# rule body terse (≤30 words) so it fits the 120-token behaviors
# budget alongside any operator-added rules.
DISCIPLINE_RULES: list[dict[str, Any]] = [
    {
        "name": "graph-tools-first",
        "kind": "operating_rule",
        "rule": (
            "Before Read/Edit/Grep on any source file, call "
            "memory_impact_check(file_path=<path>) FIRST. It returns "
            "purpose + callers + verdict in one envelope. Read is fallback "
            "for understanding algorithm logic only — not for impact analysis "
            "or symbol discovery."
        ),
        "rule_one_line": ("Call memory_impact_check before Read/Edit/Grep on source files."),
        "rationale": (
            "Read+Grep loops are 5-10x more expensive than the impact_check "
            "envelope and miss the caller / hot-symbol signal that prevents "
            "breaking changes."
        ),
        "applies_to_json": json.dumps(["source code", "refactoring", "impact analysis"]),
    },
    {
        "name": "search-before-write",
        "kind": "operating_rule",
        "rule": (
            "Before writing a new decision / theory / behavior / skill, call "
            "memory_search(query=<topic>) to surface any prior version "
            "or near-duplicate. Pass supersedes_id when replacing an existing "
            "decision; create-new only when there is no overlap."
        ),
        "rule_one_line": ("Search memory before writing a decision/theory/behavior/skill."),
        "rationale": (
            "Duplicate decisions fragment the canonical record and force "
            "agents to reason from inconsistent state."
        ),
        "applies_to_json": json.dumps(["decision writes", "theory writes", "behavior writes"]),
    },
    {
        "name": "capability-suggestion-on-write",
        "kind": "operating_rule",
        "rule": (
            "When writing a decision or theory, scan the response's "
            "capability_suggestions and record the best match in the "
            "task or plan step when it should shape execution."
        ),
        "rule_one_line": (
            "Capture relevant capability suggestions after writing a decision or theory."
        ),
        "rationale": (
            "Capability suggestions keep execution knowledge visible without "
            "depending on legacy link routes."
        ),
        "applies_to_json": json.dumps(["decision writes", "theory writes"]),
    },
    {
        "name": "maintain-plan-steps",
        "kind": "operating_rule",
        "rule": (
            "For a multi-step task, keep a live plan in plan_steps — create "
            "the steps, hold exactly one `active`, and mark each `done` / "
            "`blocked` / `skipped` as you go. The brief's Active plan "
            "section is the live tracker."
        ),
        "rule_one_line": ("Keep a live plan in plan_steps for any multi-step task."),
        "rationale": (
            "A plan no one maintains is dead weight: the brief's plan "
            "section and step-bound skill activation deliver value only "
            "when plan_steps track the work in real time."
        ),
        "applies_to_json": json.dumps(["multi-step tasks", "task planning", "plan_steps"]),
    },
]


# ============================================================
# Seed mechanics
# ============================================================


@dataclass(frozen=True, slots=True)
class SeedResult:
    """Per-rule outcome for the JSON report."""

    name: str
    status: str  # "inserted" | "skipped"
    id: str | None


def _existing_id(conn: sqlite3.Connection, *, workspace_id: str, name: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM behaviors WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return row[0] if row else None


def _insert_rule(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    rule: dict[str, Any],
) -> str:
    rule_id = f"beh_{uuid.uuid4().hex[:16]}"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """
        INSERT INTO behaviors (
            id, workspace_id, name, kind, scope, priority, rule,
            rule_one_line, rationale, applies_to_json, conflict_policy,
            source_type, source_id, source_episode_id, reviewed_by,
            reviewed_at, expires_at, confidence, importance, pinned,
            active, last_applied_at, application_count,
            created_at, updated_at, conflict_group
        ) VALUES (
            ?, ?, ?, ?, 'workspace', 'project_convention', ?, ?, ?, ?,
            'system_wins', 'seed_memory_discipline', NULL, NULL, 'system',
            ?, NULL, 0.95, 0.95, 1, 1, NULL, 0, ?, ?, NULL
        )
        """,
        (
            rule_id,
            workspace_id,
            rule["name"],
            rule["kind"],
            rule["rule"],
            rule["rule_one_line"],
            rule["rationale"],
            rule["applies_to_json"],
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return rule_id


def seed_discipline(conn: sqlite3.Connection, *, workspace_id: str) -> list[SeedResult]:
    """Idempotent seed pass. Re-running is a no-op for already-seeded rules."""
    results: list[SeedResult] = []
    for rule in DISCIPLINE_RULES:
        existing = _existing_id(conn, workspace_id=workspace_id, name=rule["name"])
        if existing:
            results.append(SeedResult(name=rule["name"], status="skipped", id=existing))
            continue
        try:
            inserted = _insert_rule(conn, workspace_id=workspace_id, rule=rule)
        except sqlite3.Error as exc:
            results.append(SeedResult(name=rule["name"], status=f"error: {exc}", id=None))
            continue
        results.append(SeedResult(name=rule["name"], status="inserted", id=inserted))
    return results


# ============================================================
# CLI
# ============================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed canonical discipline behavior_instructions for a workspace."
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--db-path",
        required=True,
        help="Path to the canonical SQLite database (must have canonical schema applied).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable report.")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    if not db_path.exists():
        sys.stderr.write(f"db not found: {db_path}\n")
        return 2
    conn = sqlite3.connect(db_path)
    try:
        results = seed_discipline(conn, workspace_id=args.workspace)
    finally:
        conn.close()

    if args.json:
        payload = [{"name": r.name, "status": r.status, "id": r.id} for r in results]
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        for r in results:
            sys.stdout.write(f"[{r.name}] {r.status} {r.id or ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
