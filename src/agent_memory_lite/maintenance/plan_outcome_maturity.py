"""Brain-pass step: feed completed plan-step outcomes into capability maturity.

Phase 5c of the plan-storage redesign. A plan step that reaches a
*terminal* status -- ``done`` or ``skipped`` -- is an outcome for every
capability (skill / role / playbook) bound to it via ``capability_links``
(Phase 4 binds the capability that serves a step). This step feeds that
outcome into the capability's maturity counters through the
``usage_tracker`` chokepoint: ``done`` -> success, ``skipped`` -> failure.

``blocked`` is deliberately NOT fed: it is a transient "stuck" state, not
a terminal one. A blocked step usually resolves to ``done`` or
``skipped`` later; feeding it now would stamp a permanent failure that
the eventual success could never correct (``outcome_fed_at`` fires once
per step). A step abandoned while blocked is marked ``skipped`` and fed
then.

Idempotent: each step is fed exactly once. ``plan_steps.outcome_fed_at``
is NULL until fed, then stamped with the pass timestamp, so a re-running
brain pass skips already-fed steps. A step with no bound capabilities is
still stamped -- there is nothing to feed, and stamping stops it being
re-scanned every pass. Failure-soft (a missing plan_steps table or
outcome_fed_at column is a no-op) and capped per pass, like every
brain-pass loop.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.capability.usage_tracker import SUPPORTED_KINDS, record_outcome
from agent_memory_lite.models.enums import CapabilityLinkTargetType
from agent_memory_lite.repositories.capability_links_repo import list_capability_links
from agent_memory_lite.repositories.plan_step_outcome_repo import (
    mark_outcome_fed,
    terminal_steps_pending_outcome,
)
from agent_memory_lite.utils.time import iso_now

# A terminal plan step is a success for the capability that served it
# only when it actually finished; a skipped step is a failure.
_SUCCESS_STATUS = "done"
# A plan step never has many bound capabilities; this just bounds the
# per-step fan-out query.
_LINKS_PER_STEP = 50


def feed_plan_step_outcomes(conn: sqlite3.Connection, *, workspace_id: str, max_steps: int) -> int:
    """Feed each not-yet-fed terminal plan step's outcome into the maturity
    counters of the capabilities bound to it.

    Returns the number of (step -> capability) outcomes actually
    recorded. A step with no bound capabilities records nothing but is
    still marked fed so it is not re-scanned. A DB without the
    ``plan_steps`` table or the ``outcome_fed_at`` column is a no-op.

    ``mark_outcome_fed`` runs *before* the per-link loop on purpose: if
    a later ``record_outcome`` ever raises, the step is already stamped,
    so the next pass skips it and the links bumped so far are never
    re-bumped -- a lost outcome (rare) is the safe failure mode, a
    double-count is not. The stamp and the bumps share one transaction
    (``run_brain_pass`` commits once, after every step), so a crash
    before that commit rolls the whole pass back atomically; do not
    insert a commit between this step and that final commit.
    """
    try:
        steps = terminal_steps_pending_outcome(conn, workspace_id, limit=max_steps)
    except sqlite3.OperationalError:
        return 0
    now_iso = iso_now()
    recorded = 0
    for step in steps:
        # Stamp first -- a step is marked the moment it is picked up, so
        # a mid-loop failure can never lead to a re-bump on the next pass.
        mark_outcome_fed(conn, workspace_id=workspace_id, step_id=step.id, fed_at=now_iso)
        success = step.status == _SUCCESS_STATUS
        links = list_capability_links(
            conn,
            workspace_id=workspace_id,
            target_type=CapabilityLinkTargetType.PLAN_STEP,
            target_id=step.id,
            limit=_LINKS_PER_STEP,
        )
        for link in links:
            kind = link.capability_type.value
            # A future CapabilityType with no maturity table would make
            # record_outcome raise -- skip it rather than abort the loop.
            if kind not in SUPPORTED_KINDS:
                continue
            if record_outcome(
                conn,
                workspace_id=workspace_id,
                kind=kind,
                capability_id=link.capability_id,
                success=success,
                episode_id=step.source_episode_id,
            ):
                recorded += 1
    return recorded
