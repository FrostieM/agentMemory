"""One-shot port: v2 ``behavior_instructions`` rows -> v3 ``behaviors`` table.

The v3 brief composer reads from ``behaviors`` (the canonical table); the
v2 envelope still reads from ``behavior_instructions``. Until this port
runs, any operator rule seeded via ``memory_upsert_behavior_instruction``
(v2 surface) is invisible to the v3 brief, even though it shows up in
the v2 envelope. On copyBot we measured 85 v2 rows (26 pinned) vs 5 v3
rows -- the operator's 26 pinned discipline rules were not riding the
brief.

This script copies every active v2 row into ``behaviors`` if a
(workspace_id, name) row does not already exist there. Idempotent --
re-running is a no-op. Does NOT drop the v2 table (v2 envelope code
still reads it; full retirement is a v4.0 task).

Schema deltas handled:

  * ``rule_one_line`` (required on v3) -- derived from ``rule`` as the
    first sentence trimmed to <= 30 words. Brief budgets behaviors at
    120 tokens; a 30-word line is ~40 tokens.
  * ``importance`` (v3 only) -- defaulted to 0.7.
  * Every other column maps 1:1 (id, workspace_id, name, kind, scope,
    priority, rule, rationale, applies_to_json, conflict_policy,
    conflict_group, source_type, source_id, source_episode_id,
    reviewed_by, reviewed_at, expires_at, confidence, pinned, active,
    last_applied_at, application_count, created_at, updated_at).

Usage::

    python scripts/migrate_behaviors_to_canonical.py --db-path <path>
    python scripts/migrate_behaviors_to_canonical.py --all  # all registered workspaces
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

_SENTENCE_END = re.compile(r"[.!?]\s|\n")


def _one_line(rule: str) -> str:
    """Derive a <=30-word one-liner from a rule body."""
    if not rule:
        return ""
    # First sentence terminator wins; otherwise truncate by word count.
    parts = _SENTENCE_END.split(rule.strip(), maxsplit=1)
    first = parts[0] if parts else rule.strip()
    words = first.split()
    if len(words) > 30:
        first = " ".join(words[:30]) + "..."
    return first.strip()


def _registered_workspaces() -> list[tuple[str, Path]]:
    """Return [(workspace_id, db_path)] from ~/.agent_memory/workspaces.json."""
    raw = os.environ.get("MEMORY_WORKSPACES_FILE")
    p = Path(raw) if raw else Path.home() / ".agent_memory" / "workspaces.json"
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[tuple[str, Path]] = []
    seen_paths: set[str] = set()
    for entry in payload.get("workspaces", []):
        if not isinstance(entry, dict):
            continue
        db = str(entry.get("db_path", ""))
        wid = str(entry.get("id", ""))
        if not db or not wid or db in seen_paths:
            continue
        seen_paths.add(db)
        out.append((wid, Path(db)))
    return out


def migrate_db(db_path: Path) -> dict[str, int]:
    """Port every active v2 behavior_instructions row missing from v3 behaviors."""
    report: dict[str, int] = {"copied": 0, "skipped_exists": 0, "skipped_inactive": 0}
    if not db_path.exists():
        return report
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        v2_rows = conn.execute(
            """
            SELECT id, workspace_id, name, kind, scope, priority, rule, rationale,
                   applies_to_json, conflict_policy, conflict_group, source_type,
                   source_id, source_episode_id, reviewed_by, reviewed_at,
                   expires_at, confidence, active, pinned, last_applied_at,
                   application_count, created_at, updated_at
            FROM behavior_instructions
            """
        ).fetchall()
        for row in v2_rows:
            if not row["active"]:
                report["skipped_inactive"] += 1
                continue
            exists = conn.execute(
                "SELECT 1 FROM behaviors WHERE workspace_id=? AND name=?",
                (row["workspace_id"], row["name"]),
            ).fetchone()
            if exists is not None:
                report["skipped_exists"] += 1
                continue
            conn.execute(
                """
                INSERT INTO behaviors (
                    id, workspace_id, name, kind, scope, priority, rule,
                    rule_one_line, rationale, applies_to_json, conflict_policy,
                    conflict_group, source_type, source_id, source_episode_id,
                    reviewed_by, reviewed_at, expires_at, confidence, importance,
                    pinned, active, last_applied_at, application_count,
                    created_at, updated_at
                ) VALUES (
                    :id, :workspace_id, :name, :kind, :scope, :priority, :rule,
                    :rule_one_line, :rationale, :applies_to_json, :conflict_policy,
                    :conflict_group, :source_type, :source_id, :source_episode_id,
                    :reviewed_by, :reviewed_at, :expires_at, :confidence, 0.7,
                    :pinned, :active, :last_applied_at, :application_count,
                    :created_at, :updated_at
                )
                """,
                {**dict(row), "rule_one_line": _one_line(row["rule"] or "")},
            )
            report["copied"] += 1
        conn.commit()
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", type=Path, help="Migrate a single DB.")
    p.add_argument("--all", action="store_true", help="Migrate every workspace in the registry.")
    p.add_argument("--json", action="store_true", help="Emit machine-readable report.")
    args = p.parse_args(argv)
    if not args.db_path and not args.all:
        p.error("pass either --db-path <path> or --all")

    targets: list[tuple[str, Path]] = (
        _registered_workspaces() if args.all else [(str(args.db_path), args.db_path)]
    )

    results: list[dict[str, object]] = []
    for label, db in targets:
        rep = migrate_db(db)
        results.append({"workspace": label, "db_path": str(db), **rep})
        if not args.json:
            sys.stdout.write(
                f"  {label:30s} copied={rep['copied']:3d} "
                f"skipped_exists={rep['skipped_exists']:3d} "
                f"skipped_inactive={rep['skipped_inactive']:3d}\n"
            )
    if args.json:
        sys.stdout.write(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
