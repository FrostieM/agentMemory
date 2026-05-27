"""memory_brief — <=500-token session-start brief composed from compact projections.

The killer feature. Replaces the verbose historical context envelope
with a tight pre-task brief assembled from SQL projections — no full
markdown bodies, no LLM call on the hot path.

Composition (token budget per layer, target): identity / behaviors /
decisions / state / code-hubs plus the v3 brain-organ layers
(associates, recent insights, watch-outs, aging, blindspots) and the
v3.1 vectors (open proposals, predictive warnings). Total target
<=500 tokens; cached on a workspace fingerprint so a write invalidates
the cached body. Sticky-brief shrinks the cap on follow-up emits in the
same session.

v3.7 SLOC decomposition: the 863-SLOC composer was split into ``brief_*``
sibling modules — brief_models / brief_tokens / brief_sticky /
brief_cache / brief_sections_core / brief_behaviors /
brief_sections_organ / brief_sections_watch / brief_assembly /
brief_compose / brief_skill. This module is now the facade: it
re-exports the full public + internal surface (see ``__all__``) so every
existing import site, and the tests that reach into brief internals via
``brief_mod.<name>``, are unaffected.
"""

from __future__ import annotations

from agent_memory_lite.cognition.brief_assembly import (
    _REDISTRIBUTE_RECIPIENTS,
    _build_all_sections,
    _redistribute_and_rebuild,
)
from agent_memory_lite.cognition.brief_behaviors import (
    _PRIORITY_WEIGHT,
    _applies_to_csv,
    _build_pinned_behaviors,
    _collect_pinned_behaviors,
    _iso_to_sort_key,
)
from agent_memory_lite.cognition.brief_cache import (
    _BRIEF_CACHE,
    _BRIEF_CACHE_LOCK,
    _BRIEF_CACHE_MAX,
    _BRIEF_ENV_KEYS,
    _brief_env_signature,
    _cache_remember,
    _workspace_fingerprint,
)
from agent_memory_lite.cognition.brief_compose import DEFAULT_TOKEN_BUDGET, compose_brief
from agent_memory_lite.cognition.brief_models import Brief, BriefSection
from agent_memory_lite.cognition.brief_sections_core import (
    _SELF_MODEL_BRIEF_WORDS,
    _build_code_hubs,
    _build_identity,
    _build_state,
    _build_top_decisions,
)
from agent_memory_lite.cognition.brief_sections_organ import (
    _build_associates,
    _build_recent_insights,
    iso_now_for_brief,
)
from agent_memory_lite.cognition.brief_sections_plan import _build_active_plan
from agent_memory_lite.cognition.brief_sections_watch import (
    _build_aging_decisions,
    _build_blindspots,
    _build_watch_outs,
)
from agent_memory_lite.cognition.brief_skill import fetch_skill_body
from agent_memory_lite.cognition.brief_sticky import (
    _SESSION_SEEN_LOCK,
    _session_seen,
    _sticky_brief_enabled,
    _sticky_followup_tokens,
    reset_session_seen,
)
from agent_memory_lite.cognition.brief_tokens import approx_tokens, fit_to_budget

# Re-exported under their _build_* aliases (the names the original
# brief.py exposed); imported from brief_v3_1 — their real home — so the
# facade does not depend on brief_assembly's private re-imports.
from agent_memory_lite.cognition.brief_v3_1 import (
    build_open_proposals as _build_open_proposals,
)
from agent_memory_lite.cognition.brief_v3_1 import (
    build_predictive_warnings as _build_predictive_warnings,
)

# __all__ deliberately includes private (_-prefixed) names: the brief
# test-suite and the brain crash/probe scripts reach into brief
# internals via ``brief_mod.<name>`` (e.g. _BRIEF_CACHE, _build_*,
# reset_session_seen). Listing every re-exported name keeps that
# back-compat contract and keeps ruff/mypy from flagging the re-export
# imports as unused.
__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "_BRIEF_CACHE",
    "_BRIEF_CACHE_LOCK",
    "_BRIEF_CACHE_MAX",
    "_BRIEF_ENV_KEYS",
    "_PRIORITY_WEIGHT",
    "_REDISTRIBUTE_RECIPIENTS",
    "_SELF_MODEL_BRIEF_WORDS",
    "_SESSION_SEEN_LOCK",
    "Brief",
    "BriefSection",
    "_applies_to_csv",
    "_brief_env_signature",
    "_build_active_plan",
    "_build_aging_decisions",
    "_build_all_sections",
    "_build_associates",
    "_build_blindspots",
    "_build_code_hubs",
    "_build_identity",
    "_build_open_proposals",
    "_build_pinned_behaviors",
    "_build_predictive_warnings",
    "_build_recent_insights",
    "_build_state",
    "_build_top_decisions",
    "_build_watch_outs",
    "_cache_remember",
    "_collect_pinned_behaviors",
    "_iso_to_sort_key",
    "_redistribute_and_rebuild",
    "_session_seen",
    "_sticky_brief_enabled",
    "_sticky_followup_tokens",
    "_workspace_fingerprint",
    "approx_tokens",
    "compose_brief",
    "fetch_skill_body",
    "fit_to_budget",
    "iso_now_for_brief",
    "reset_session_seen",
]
