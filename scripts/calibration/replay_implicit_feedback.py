"""Replay audit_log + capability_links into implicit feedback rows.

Simulates 'flag was on the whole time': walks past operator actions and
generates the memory_usage_feedback rows that record_implicit_archive /
_promote / _link would have written. Pure replay, no LLM, no HTTP.

Usage:
    python scripts/calibration/replay_implicit_feedback.py \
        --db <path> --workspace <id>

Historical calibration evidence lives in git history.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.implicit_feedback import (
    record_implicit_archive,
    record_implicit_link,
    record_implicit_promote,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite DB path")
    parser.add_argument("--workspace", required=True, help="workspace_id to replay")
    args = parser.parse_args()
    db_path = Path(args.db)
    workspace_filter = args.workspace
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_IMPLICIT_FEEDBACK_ENABLED="true",
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row

    archived = 0
    promoted = 0
    linked = 0

    # 1) archives -> -1.0
    rows = conn.execute(
        "SELECT workspace_id, target_type, target_id "
        "FROM audit_log WHERE action='archive' AND workspace_id = ?",
        (workspace_filter,),
    ).fetchall()
    for r in rows:
        if record_implicit_archive(
            conn,
            settings=settings,
            workspace_id=r["workspace_id"],
            source_type=r["target_type"],
            source_id=r["target_id"],
        ):
            archived += 1

    # 2) promotes -> +0.7
    rows = conn.execute(
        "SELECT workspace_id, after_json FROM audit_log "
        "WHERE action='promote_memory_candidate' AND workspace_id = ?",
        (workspace_filter,),
    ).fetchall()
    for r in rows:
        after = json.loads(r["after_json"]) if r["after_json"] else {}
        target_type = after.get("promoted_target_type")
        target_id = after.get("promoted_target_id")
        if not target_type or not target_id:
            continue
        if record_implicit_promote(
            conn,
            settings=settings,
            workspace_id=r["workspace_id"],
            source_type=target_type,
            source_id=target_id,
        ):
            promoted += 1

    # 3) capability_links -> +strength
    rows = conn.execute(
        "SELECT workspace_id, target_type, target_id, strength "
        "FROM capability_links WHERE workspace_id = ?",
        (workspace_filter,),
    ).fetchall()
    for r in rows:
        if record_implicit_link(
            conn,
            settings=settings,
            workspace_id=r["workspace_id"],
            target_type=r["target_type"],
            target_id=r["target_id"],
            strength=float(r["strength"]),
        ):
            linked += 1

    total = conn.execute("SELECT COUNT(*) FROM memory_usage_feedback").fetchone()[0]
    print(f"replayed: archive={archived} promote={promoted} link={linked}")
    print(f"total memory_usage_feedback rows now: {total}")

    # Distribution by source
    for r in conn.execute(
        "SELECT source, COUNT(*), AVG(usefulness) FROM memory_usage_feedback "
        "GROUP BY source ORDER BY 2 DESC"
    ):
        print(f"  source={r[0]:20s} n={r[1]:3d} avg_usefulness={round(r[2], 3)}")

    conn.close()


if __name__ == "__main__":
    main()
