"""Detect facts that should be closed when a new fact arrives.

Phase 4 rule: any prior open fact with the same `(subject_entity_id, relation)`
is invalidated. We do NOT compare values — the latest write wins. This matches
the spec's "same subject + same relation + incompatible object" pattern for
single-valued relations (USES, RUNS_IN, IMPLEMENTS, DEPENDS_ON, …). Multi-valued
relations should use distinct relation names per slot.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.facts import Fact
from agent_memory_lite.repositories.facts_repo import find_open_facts


def find_conflicting_facts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    subject_entity_id: str,
    relation: str,
) -> list[Fact]:
    return find_open_facts(
        conn,
        workspace_id=workspace_id,
        subject_entity_id=subject_entity_id,
        relation=relation,
    )
