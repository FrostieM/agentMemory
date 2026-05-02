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
from agent_memory_lite.repositories.entities_repo import (
    find_entity_by_name,
    get_entity,
    insert_entity_row,
    list_entities,
    update_entity_meta,
)
from agent_memory_lite.repositories.episodes_repo import (
    get_episode,
    insert_episode,
    list_recent_episodes,
)
from agent_memory_lite.repositories.facts_repo import (
    close_fact,
    find_open_facts,
    get_fact,
    insert_fact_row,
    list_active_facts,
    list_all_facts_for_subjects,
)
from agent_memory_lite.repositories.files_repo import (
    delete_file,
    get_file_by_path,
    list_files,
    upsert_file_row,
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
    "close_fact",
    "deactivate_for_key",
    "deactivate_rule",
    "delete_chunks_by_episode",
    "delete_chunks_by_file",
    "delete_file",
    "find_entity_by_name",
    "find_open_facts",
    "get_active_by_key",
    "get_chunk",
    "get_decision",
    "get_entity",
    "get_episode",
    "get_fact",
    "get_file_by_path",
    "get_task_state",
    "insert_audit",
    "insert_chunk",
    "insert_decision_row",
    "insert_entity_row",
    "insert_episode",
    "insert_fact_row",
    "insert_procedural_rule_row",
    "list_active_core",
    "list_active_decisions",
    "list_active_facts",
    "list_active_rules",
    "list_active_task_states",
    "list_all_decisions",
    "list_all_facts_for_subjects",
    "list_entities",
    "list_files",
    "list_recent_episodes",
    "update_entity_meta",
    "upsert_core_memory_row",
    "upsert_file_row",
    "upsert_task_state_row",
]
