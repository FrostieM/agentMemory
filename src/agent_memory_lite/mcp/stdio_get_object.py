"""Local fallback for memory_get_object.

The HTTP service has the canonical /memory/get_object route; this
module is the in-process mirror used when HTTP delegation is
unavailable. Pulled out of the episodes handler module to keep both
files under the SLOC ceiling.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def _local_get_object(
    db: sqlite3.Connection, kind: str, object_id: str, *, include_evidence: bool
) -> dict[str, Any] | None:
    """Mirror /memory/get_object's repo dispatch."""
    from agent_memory_lite.repositories.behavior_repo import (  # noqa: PLC0415
        get_behavior_instruction_by_id,
    )
    from agent_memory_lite.repositories.capabilities_repo import (  # noqa: PLC0415
        get_playbook_by_id,
        get_role_by_id,
        get_skill_by_id,
    )
    from agent_memory_lite.repositories.decisions_repo import get_decision  # noqa: PLC0415
    from agent_memory_lite.repositories.research_repo import (  # noqa: PLC0415
        get_concept_by_id,
        get_experiment,
        get_insight,
        get_snapshot,
    )
    from agent_memory_lite.repositories.theories_repo import (  # noqa: PLC0415
        get_theory,
        list_evidence_for_theory,
    )

    fetchers: dict[str, Any] = {
        "decision": get_decision,
        "snapshot": get_snapshot,
        "experiment": get_experiment,
        "insight": get_insight,
        "concept": get_concept_by_id,
        "role": get_role_by_id,
        "skill": get_skill_by_id,
        "playbook": get_playbook_by_id,
        "behavior_instruction": get_behavior_instruction_by_id,
    }
    if kind == "theory":
        theory = get_theory(db, object_id)
        if theory is None:
            return None
        body = theory.model_dump(mode="json")
        if include_evidence:
            body["evidence"] = [
                ev.model_dump(mode="json")
                for ev in list_evidence_for_theory(db, theory.id, limit=20)
            ]
        return body
    fetcher = fetchers.get(kind)
    if fetcher is None:
        raise ValueError(f"unsupported kind: {kind!r}")
    item = fetcher(db, object_id)
    return item.model_dump(mode="json") if item is not None else None
