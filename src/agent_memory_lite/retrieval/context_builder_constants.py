"""Per-section caps + intent keywords for the context builder.

Pulled out of the monolithic ``context_builder.py`` so the rendering and
fitting logic can stay close to the SLOC ceiling. These are pure data;
nothing here imports back into the renderer modules.
"""

from __future__ import annotations

MAX_FTS_HITS = 30
MAX_VECTOR_HITS = 30
MAX_GRAPH_HITS = 40
# Per-section caps. Token budgeting trims further when needed; raising
# the ceiling lets a workspace with many active records actually surface
# more than a token-skeleton handful when the budget is generous. The
# previous values (4 decisions of 89, 1 of each capability kind out of
# dozens) starved the agent of real context even with max_tokens=6000.
MAX_DECISIONS = 10
MAX_HISTORICAL_DECISIONS = 20
MAX_THEORIES = 6
MAX_THEORY_EVIDENCE = 3
MAX_RESEARCH_AGENDA = 5
MAX_BEHAVIOR_INSTRUCTIONS = 18
MAX_AGENT_CAPABILITIES = 6
MAX_TITLE_CHARS = 180
MAX_TEXT_CHARS = 280
MAX_COMMAND_CHARS = 180
# Per-item list cap (responsibilities / predictions / applies_to / ...).
# 1 was effectively "show one item, hide the rest"; 5 covers most real
# objects without bloating the envelope.
MAX_LIST_ITEMS = 5
MAX_LIST_ITEM_CHARS = 140
MAX_CHUNK_TEXT_CHARS = 1200
MAX_STUB_CHUNK_TEXT_CHARS = 360
MAX_FULL_DECISION_ITEMS = 3
MIN_CHUNK_RESERVE_TOKENS = 384
MAX_CHUNK_RESERVE_TOKENS = 1200
STRUCTURED_SAFETY_RESERVE_TOKENS = 128
# Discover-then-fetch: fetch a wider slice of relevant items per section,
# split into full-render (top-N) + index-render (the rest). The agent
# sees compact <ref id="..." title="..."/> entries for every relevant
# item that didn't fit the full render and can call memory_get_<kind>(id)
# to expand any of them on demand. This stops the hard caps from
# silently dropping the long tail.
INDEX_FETCH_LIMIT = 50  # how many items the repo returns per section
INDEX_REFS_PER_SECTION = 20  # how many <ref/> entries we render after full items
INDEX_REF_TITLE_CHARS = 80
LOW_CONFIDENCE_STALE_SCORE = 0.50
LOW_CONFIDENCE_RECENT_SCORE = 0.35
LOW_CONFIDENCE_VECTOR_SCORE = 0.25
LOW_CONFIDENCE_STALE_DAYS = 14
KEEP_EXACT_FTS_RANK_BELOW = 3
MOJIBAKE_REPLACEMENT_MIN = 3
MOJIBAKE_REPLACEMENT_RATIO = 0.005
RENDER_LEVEL_RANK = {"none": 0, "stub": 1, "summary": 2, "full": 3}
INTENT_KEYWORDS = {
    "research": (
        "agenda",
        "cohort",
        "evidence",
        "experiment",
        "hypothesis",
        "replay",
        "research",
        "theory",
        "theories",
        "snapshot",
        "source-flip",
        "shadow",
    ),
    "capability": (
        "behavior",
        "capability",
        "instruction",
        "instructions",
        "playbook",
        "role",
        "roles",
        "skill",
        "skills",
    ),
    "architecture": (
        "api",
        "architecture",
        "decision",
        "deploy",
        "migration",
        "runtime",
        "vps",
    ),
    "incident": (
        "504",
        "feed down",
        "hang",
        "health",
        "incident",
        "timeout",
        "watchdog",
        "wedged",
    ),
}
