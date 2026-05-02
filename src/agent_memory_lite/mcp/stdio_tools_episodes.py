"""Tool schemas for the episodes / context / search / get_object tools."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

EPISODE_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_get_context",
        description=(
            "Retrieve the agent's memory context for the given query. Returns an "
            "XML envelope with core_memory, behavior_instructions, task_state, "
            "active_decisions, active_theories, research_agenda, "
            "agent_capabilities, procedural_rules, retrieved_facts, and "
            "retrieved_chunks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "task_id": {"type": "string"},
                "query": {"type": "string", "minLength": 1},
                "files_in_scope": {"type": "array", "items": {"type": "string"}},
                "max_tokens": {
                    "type": "integer",
                    "minimum": 200,
                    "maximum": 32000,
                    "default": 3500,
                },
                "historical": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="memory_search",
        description="Exact FTS lookup over chunks (BM25 ordered).",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 200},
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="memory_ingest_episode",
        description=(
            "Persist an event into episodic memory. Secrets are redacted server "
            "side before storage, embedding, and FTS indexing."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "session_id": {"type": "string"},
                "task_id": {"type": "string"},
                "source_type": {"type": "string", "default": "agent_action"},
                "raw_text": {"type": "string", "minLength": 1},
                "trust_level": {"type": "string", "default": "agent_observed"},
                "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5},
            },
            "required": ["raw_text"],
        },
    ),
    types.Tool(
        name="memory_ingest_file",
        description="Index a single file (idempotent by content_hash).",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
                "language": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    ),
    types.Tool(
        name="memory_get_object",
        description=(
            "Discover-then-fetch: pull the full body of one memory object by "
            "(kind, id). Use this when memory_get_context's <index> block "
            "lists a <ref id=... title=.../> you want to expand, instead of "
            "re-querying with a fuzzy memory_list_*."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "kind": {
                    "type": "string",
                    "enum": [
                        "decision",
                        "theory",
                        "snapshot",
                        "experiment",
                        "insight",
                        "concept",
                        "role",
                        "skill",
                        "playbook",
                        "behavior_instruction",
                    ],
                },
                "id": {"type": "string", "minLength": 1},
                "include_evidence": {"type": "boolean", "default": False},
            },
            "required": ["kind", "id"],
        },
    ),
]
