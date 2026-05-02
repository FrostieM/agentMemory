"""Telemetry helper for the context routes.

Split out of ``context.py`` so the route file stays under the SLOC
ceiling. ``trace_used_context_objects`` mirrors retrieved-object
metadata into the live UI trace so the observatory can render labels
and counts identically to the source rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_memory_lite.api.ui_telemetry import MemoryOperationTrace

_TABLE_TO_OBJECT_TYPE = {
    "episodes": "episode",
    "chunks": "chunk",
    "files": "file",
    "decisions": "decision",
    "theories": "theory",
    "theory_evidence": "theory_evidence",
    "research_experiments": "experiment",
    "experiment_results": "experiment_result",
    "memory_snapshots": "snapshot",
    "research_insights": "insight",
    "domain_concepts": "concept",
    "agent_roles": "role",
    "agent_skills": "skill",
    "agent_playbooks": "playbook",
    "capability_links": "capability_link",
    "task_state": "task_state",
    "behavior_instructions": "behavior_instruction",
    "maintenance_events": "maintenance_event",
    "procedural_rules": "procedural_rule",
    "retrieved_facts": "retrieved_fact",
}


def _object_type_for_table(table: str) -> str:
    return _TABLE_TO_OBJECT_TYPE.get(table, table.rstrip("s") or "memory_object")


def trace_used_context_objects(
    trace: MemoryOperationTrace,
    objects: Sequence[Any],
    *,
    limit: int = 24,
) -> None:
    for item in objects[:limit]:
        trace.graph_delta(
            object_type=_object_type_for_table(item.table),
            object_id=item.id,
            action="used",
            label=item.label,
            stage="context",
            counts={
                "table": item.table,
                # Mirror the clean per-object label into counts so the UI
                # picks it up via counts.label (its first preference).
                # Without this, the UI falls back to the request snippet,
                # which is the user's query text — and if that query came
                # in with a broken encoding from an upstream client, the
                # node labels would render as mojibake even though the
                # real labels live cleanly in SQLite.
                "label": item.label,
                "relation": item.relation,
                "rank": item.rank,
                "updated_at": item.updated_at or "",
            },
        )
