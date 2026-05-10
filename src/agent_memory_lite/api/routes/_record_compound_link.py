"""Optional capability-link step for ``/memory/record_with_evidence``.

Lifted out of ``record_compound.py`` so the route file stays at or
below the 150-SLOC ceiling. The compound route owns three writers
(episode + decision + capability link) plus their tracing — this
module owns the third writer's gating + persistence + audit.

Triplet completeness is already enforced by the schema validator
(``RecordWithEvidenceRequest._capability_triplet``); here we just
gate on presence. ``None`` returns mean "no link requested" — the
caller treats it as a non-link path and surfaces capability
suggestions instead.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.api.schemas.record_compound import RecordWithEvidenceRequest
from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.models.enums import CapabilityLinkTargetType


def optionally_link_capability(
    conn: sqlite3.Connection,
    *,
    body: RecordWithEvidenceRequest,
    decision_id: str,
    episode_id: str,
    trace: Any,
) -> str | None:
    """Persist a capability link when the body carries the full triplet."""
    if (
        body.capability_type is None
        or body.capability_name is None
        or body.capability_relation is None
    ):
        return None
    trace.stage_started("capability_link", "Link decision to capability")
    link = link_capability(
        conn,
        CapabilityLinkIn(
            workspace_id=body.workspace_id,
            target_type=CapabilityLinkTargetType.DECISION,
            target_id=decision_id,
            capability_type=body.capability_type,
            capability_name=body.capability_name,
            relation=body.capability_relation,
            rationale=body.capability_rationale,
            strength=body.capability_strength,
            source_episode_id=episode_id,
        ),
    )
    trace.stage_done(
        "capability_link",
        "Capability link persisted",
        counts={"link_id": link.id, "relation": link.relation},
    )
    return link.id
