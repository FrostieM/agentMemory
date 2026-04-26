"""Thin SQL wrappers, one per table. No business logic lives here."""

from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.chunks_repo import (
    delete_chunks_by_episode,
    delete_chunks_by_file,
    get_chunk,
    insert_chunk,
)
from agent_memory_lite.repositories.core_memory_repo import (
    deactivate_for_key,
    get_active_by_key,
    list_active_core,
    upsert_core_memory_row,
)
from agent_memory_lite.repositories.decisions_repo import (
    close_decision,
    get_decision,
    insert_decision_row,
    list_active_decisions,
    list_all_decisions,
)
from agent_memory_lite.repositories.episodes_repo import (
    get_episode,
    insert_episode,
    list_recent_episodes,
)
from agent_memory_lite.repositories.procedural_repo import (
    deactivate_rule,
    insert_procedural_rule_row,
    list_active_rules,
)
from agent_memory_lite.repositories.task_state_repo import (
    get_task_state,
    list_active_task_states,
    upsert_task_state_row,
)

__all__ = [
    "close_decision",
    "deactivate_for_key",
    "deactivate_rule",
    "delete_chunks_by_episode",
    "delete_chunks_by_file",
    "get_active_by_key",
    "get_chunk",
    "get_decision",
    "get_episode",
    "get_task_state",
    "insert_audit",
    "insert_chunk",
    "insert_decision_row",
    "insert_episode",
    "insert_procedural_rule_row",
    "list_active_core",
    "list_active_decisions",
    "list_active_rules",
    "list_active_task_states",
    "list_all_decisions",
    "list_recent_episodes",
    "upsert_core_memory_row",
    "upsert_task_state_row",
]
