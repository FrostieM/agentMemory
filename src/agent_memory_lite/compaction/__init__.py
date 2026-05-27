"""Memory compaction.

Three operations bundled by `memory_compact`:
- summarize old episodes into a single SUMMARY episode per period;
- archive long-invalidated facts (cosmetic — they were already hidden by
  filters, but archive trims `list_active_facts` scans and keeps the audit
  trail honest);
- promote durable, high-trust candidates to behaviors.
"""

from agent_memory_lite.compaction.invalidate_stale import (
    StaleFactStats,
    archive_stale_facts,
)
from agent_memory_lite.compaction.promote_durable import (
    PromotionStats,
    promote_durable_candidates,
)
from agent_memory_lite.compaction.summarize_old import (
    SummarizationStats,
    summarize_old_episodes,
)

__all__ = [
    "PromotionStats",
    "StaleFactStats",
    "SummarizationStats",
    "archive_stale_facts",
    "promote_durable_candidates",
    "summarize_old_episodes",
]
