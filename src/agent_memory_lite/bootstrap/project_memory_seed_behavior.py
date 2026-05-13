"""Aggregator for generic-discipline behavior_instruction templates.

Phase 1.2 of v2.2 consolidation: the original module hit 297 lines with
4 factories so it was split into two by-concern submodules:

* ``project_memory_seed_behavior_writes.py`` — write-discipline rules
  (search-before-write, link-capability-after-write).
* ``project_memory_seed_behavior_workflow.py`` — workflow / shipping
  discipline (memory-first-before-edit, no-unauthorized-git-push).

This file re-exports the factories and owns the two registries:

* ``DISCIPLINE_FACTORIES`` — every factory the seed orchestrator should
  upsert (4 currently).
* ``PINNED_DISCIPLINE_FACTORIES`` — subset whose output must also be
  pinned after upsert so it rides every active envelope regardless of
  query relevance. Reserved for shipping-critical rules that must
  never be missed on a noisy query.

Adding a new generic-discipline rule is two lines: define the factory
in the appropriate submodule, then append it to ``DISCIPLINE_FACTORIES``
(and optionally ``PINNED_DISCIPLINE_FACTORIES``) below.
"""

from __future__ import annotations

from agent_memory_lite.bootstrap.project_memory_seed_behavior_post_write import (
    memory_write_resolve_candidates_instruction,
)
from agent_memory_lite.bootstrap.project_memory_seed_behavior_pre_action import (
    applies_to_checklist_verbatim_instruction,
    verification_claims_cite_evidence_instruction,
)
from agent_memory_lite.bootstrap.project_memory_seed_behavior_workflow import (
    memory_first_before_edit_instruction,
    no_unauthorized_git_push_instruction,
)
from agent_memory_lite.bootstrap.project_memory_seed_behavior_writes import (
    link_capability_discipline_instruction,
    search_before_write_discipline_instruction,
)

__all__ = [
    "DISCIPLINE_FACTORIES",
    "PINNED_DISCIPLINE_FACTORIES",
    "applies_to_checklist_verbatim_instruction",
    "link_capability_discipline_instruction",
    "memory_first_before_edit_instruction",
    "memory_write_resolve_candidates_instruction",
    "no_unauthorized_git_push_instruction",
    "search_before_write_discipline_instruction",
    "verification_claims_cite_evidence_instruction",
]


# Every factory the seed orchestrator should upsert. Order is stable to
# keep upsert-by-name behaviour deterministic across runs.
DISCIPLINE_FACTORIES = (
    link_capability_discipline_instruction,
    search_before_write_discipline_instruction,
    memory_first_before_edit_instruction,
    no_unauthorized_git_push_instruction,
    applies_to_checklist_verbatim_instruction,
    verification_claims_cite_evidence_instruction,
    memory_write_resolve_candidates_instruction,
)


# Subset of DISCIPLINE_FACTORIES whose output should be pinned after
# upsert. Pinned behavior_instructions ride every active envelope
# regardless of query relevance — appropriate for shipping-discipline
# rules that must never be missed on a noisy query.
PINNED_DISCIPLINE_FACTORIES = (
    memory_first_before_edit_instruction,
    no_unauthorized_git_push_instruction,
    applies_to_checklist_verbatim_instruction,
    verification_claims_cite_evidence_instruction,
    memory_write_resolve_candidates_instruction,
)
