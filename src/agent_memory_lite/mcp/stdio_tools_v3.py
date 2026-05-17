"""MCP tool schemas for the v3 agent surface.

Prefix ``memory_v3_*`` while v2 names still ship in the same stdio
server. At v4.0 cutover the v2 tools are removed and v3 tools can
be renamed to their canonical short names. Until then, the prefix
makes the surface unambiguous in the agent's tool list.

The v3 surface is intentionally minimal:

  9 MCP tools = 6 strict + 2 hook primitives + 1 invoke

Strict 6:
  memory_v3_search, memory_v3_get, memory_v3_write,
  memory_v3_edit,   memory_v3_pin, memory_v3_archive

Hook primitives (also exposed as MCP for ease of access):
  memory_v3_brief, memory_v3_lint

Skill body fetch (full body — opt-in via explicit invoke):
  memory_v3_invoke_skill

list / count / rollback / versions stay HTTP-only — accessible via
``memory-cli`` for ops, but not in the hot MCP path. Keeps the
agent's tool list small (single-screen).
"""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

_KINDS = [
    "decision",
    "theory",
    "behavior",
    "skill",
    "episode",
    "concept",
    "task",
    "insight",
    "code_digest",
    "chunk",
]


V3_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_v3_search",
        description=(
            "v3 search — BM25 + projection. Returns a list of compact "
            "projections (~30 tokens per hit) with scores. Pass ``kinds`` "
            "to restrict the search to specific kinds; default = all. "
            "Set ``rerank=true`` to run the optional cross-encoder "
            "reranker (requires the [rerank] extra)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "query": {"type": "string", "minLength": 1},
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": _KINDS},
                },
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                "rerank": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="memory_v3_get",
        description=(
            "v3 get — fetch one row by id. Default = compact projection "
            "(~30 tokens). Pass ``fields`` (CSV string OR list) to "
            "selectively fetch full columns like decision_text, rationale, "
            "body_md."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "kind": {"type": "string", "enum": _KINDS},
                "id": {"type": "string", "minLength": 1},
                "fields": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "Comma-separated string OR array of column names.",
                },
            },
            "required": ["kind", "id"],
        },
    ),
    types.Tool(
        name="memory_v3_write",
        description=(
            "v3 write — create one row of the given kind. Computes the "
            "gist column on write (no on-the-fly summarization at read). "
            "Snapshots prior content to ``versions`` table. Returns "
            "the compact projection of the new row."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "kind": {"type": "string", "enum": _KINDS},
                "payload": {
                    "type": "object",
                    "description": "Kind-specific structured fields.",
                },
                "agent_id": {"type": "string", "default": "mcp"},
                "source_episode_id": {"type": "string"},
            },
            "required": ["kind", "payload"],
        },
    ),
    types.Tool(
        name="memory_v3_edit",
        description=(
            "v3 edit — partial update on an existing row. Snapshots "
            "prior content to ``versions`` table before write. "
            "Re-computes gist columns when source fields change. "
            "Returns the compact projection of the resulting row."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "kind": {"type": "string", "enum": _KINDS},
                "id": {"type": "string", "minLength": 1},
                "fields": {
                    "type": "object",
                    "description": "Column → new-value map. Only listed columns are updated.",
                },
                "agent_id": {"type": "string", "default": "mcp"},
            },
            "required": ["kind", "id", "fields"],
        },
    ),
    types.Tool(
        name="memory_v3_pin",
        description=(
            "v3 pin — toggle pinned bit on a decision or behavior. "
            "Pinned rows ride every brief section regardless of recency "
            "or relevance. Pass ``pinned=false`` to unpin."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "kind": {"type": "string", "enum": ["decision", "behavior"]},
                "id": {"type": "string", "minLength": 1},
                "pinned": {"type": "boolean", "default": True},
                "agent_id": {"type": "string", "default": "mcp"},
            },
            "required": ["kind", "id"],
        },
    ),
    types.Tool(
        name="memory_v3_archive",
        description=(
            "v3 archive — mark a row as archived so it stops showing in "
            "brief / list / search by default but stays retrievable by "
            "id. Pass ``reason`` to record why."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "kind": {"type": "string", "enum": _KINDS},
                "id": {"type": "string", "minLength": 1},
                "reason": {"type": "string"},
                "agent_id": {"type": "string", "default": "mcp"},
            },
            "required": ["kind", "id"],
        },
    ),
    types.Tool(
        name="memory_v3_brief",
        description=(
            "v3 brief — ≤max_tokens session-start brief composed from "
            "compact projections. Sections: identity, behaviors, "
            "decisions, state, code_hubs. Hook primitive for "
            "UserPromptSubmit / session-start injection."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "task": {"type": "string"},
                "max_tokens": {
                    "type": "integer",
                    "default": 500,
                    "minimum": 100,
                    "maximum": 2000,
                },
            },
            "required": [],
        },
    ),
    types.Tool(
        name="memory_v3_lint",
        description=(
            "v3 lint — pre-task advisory. Returns {verdict, "
            "applicable_rules, related_decisions, prior_failures, "
            "watch_outs}. Hook primitive for PreToolUse wiring; safe "
            "to call advisory-only when there is no PreToolUse hook."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "tool_name": {"type": "string", "minLength": 1},
                "tool_payload": {"type": "object"},
                "transcript_path": {"type": "string"},
            },
            "required": ["tool_name", "tool_payload"],
        },
    ),
    types.Tool(
        name="memory_v3_invoke_skill",
        description=(
            "v3 invoke skill — fetch full body_md of a skill and bump "
            "usage_count + last_invoked_at. The ONLY surface that "
            "returns full markdown body; every other tool returns "
            "compact projections."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "skill_id": {"type": "string", "minLength": 1},
            },
            "required": ["skill_id"],
        },
    ),
]
