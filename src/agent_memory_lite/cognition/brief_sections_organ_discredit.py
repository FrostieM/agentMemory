"""Discredited-neighbour filter for the brief associates substrate.

Extracted from cognition/brief_sections_organ.py during the SLOC
decomposition. Pure status/flag logic with no DB access -- decides whether
a spreading-activation neighbour is dead signal that must not surface as a
positive association.
"""

from __future__ import annotations

# The canonical terminal/dead status DENYLIST for the knowledge kinds whose
# live states are many and dead states few: archived/superseded/rejected for
# decisions+insights, plus 'weakened' for theories (theories_repo flips a theory
# to 'weakened' on refuting evidence, and outcome_recompute / lint / self_model
# all bucket weakened WITH rejected as a hard-negative). A freshly-weakened
# theory's outcome_score is not recomputed negative until the next brain pass,
# so without the status arm it would slip through the outcome arm and surface as
# a positive associate in that window.
_DEAD_STATUSES = frozenset({"superseded", "archived", "rejected", "weakened"})

# The work-item kinds invert that: few live states, many terminal ones
# (done/cancelled/skipped/closed/fixed/wontfix/...). An ALLOWLIST of live
# statuses is safer than a denylist -- a terminal status added later is
# automatically dead. The live sets match the brief's own section filters
# (brief_sections_core/plan: tasks IN (active,in_progress); plan_steps IN
# (active,pending,blocked) per hygiene; brief_sections_watch: issues IN
# (open,in_progress)). 'blocked' is a LIVE plan-step state (a current obstacle),
# not terminal.
_LIVE_STATUS_BY_KIND: dict[str, frozenset[str]] = {
    "task": frozenset({"active", "in_progress"}),
    "plan_step": frozenset({"active", "pending", "blocked"}),
    "issue": frozenset({"open", "in_progress"}),
}


def _is_discredited(proj: dict[str, object]) -> bool:
    """A neighbor that must not be surfaced as a positive association. Covers
    every kind reachable in the associates substrate, by its terminal mechanism:

    * status DENYLIST (_DEAD_STATUSES) -- decision / theory / insight
    * status ALLOWLIST (_LIVE_STATUS_BY_KIND) -- task / plan_step / issue
    * active=0 -- the active-flag kinds behavior / skill / concept
    * is_archived=1 -- episode / chunk
    * non-pinned negative outcome_score -- the outcome-bearing kinds

    (code_digest carries no dead state -- it is hard-pruned when its file is
    deleted -- so it is intentionally never discredited here.) Status is
    case-folded (and whitespace-stripped) so a mixed-case or padded label
    cannot slip a dead row through, nor over-filter a padded live one."""
    status = str(proj.get("status") or "").strip().lower()
    # Knowledge kinds: terminal-status denylist.
    if status in _DEAD_STATUSES:
        return True
    # active-flag kinds (behavior/skill/concept): active=0 is terminal. Their
    # projections emit 'active'; kinds without an active flag return None here,
    # which this skips. A deactivated row can keep its default 0.0 outcome_score
    # (e.g. a behavior auto-archived for never firing, or an archived concept/
    # skill), so the outcome arm below would not catch it.
    if proj.get("active") is False:
        return True
    # episode/chunk: is_archived=1 is terminal (those tables have no status).
    if proj.get("is_archived") is True:
        return True
    # Work-item kinds: any status outside the live allowlist is terminal.
    kind = proj.get("kind")
    if isinstance(kind, str):
        live = _LIVE_STATUS_BY_KIND.get(kind)
        if live is not None and status not in live:
            return True
    # Terminal checks precede the pinned bypass (a pinned-but-dead row is still
    # dead); the outcome arm is last.
    if proj.get("pinned"):
        return False
    raw = proj.get("outcome_score")
    return isinstance(raw, (int, float)) and float(raw) < 0.0
