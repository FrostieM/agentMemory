"""1.7.0: soft graph — accumulated co-change / co-reference signals.

The hard graph (``symbol_edges``) records EXPLICIT relationships:
function A calls function B because the AST has a call_expression
inside A's body whose target resolves to B. The soft graph captures
HEURISTIC relationships:

* ``co_changed``       — A and B were rewritten in the same file
                          ingest pass (likely belong together)
* ``co_referenced``    — A and B are both referenced from the same
                          third symbol (siblings under a common parent)
* ``similar_signature`` — A and B share signature_text shape (likely
                          parallel implementations of the same idea)

Each pair carries a ``weight`` that accumulates with each
observation; ``last_seen_at`` lets future passes apply EWMA decay.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ALLOWED_SOFT_KINDS: frozenset[str] = frozenset({"co_changed", "co_referenced", "similar_signature"})


class SoftEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    src_qualified_name: str
    dst_qualified_name: str
    edge_kind: str
    weight: float
    observation_count: int
    last_seen_at: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
