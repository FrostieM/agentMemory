"""MCP tool schemas for the canonical agent surface.

Names are version-free (``memory_*``, not ``memory_v3_*``). The
surface is intentionally minimal:

  12 MCP tools = 6 strict + 2 hook primitives + 4 specialised

Strict 6:
  memory_search, memory_get, memory_write,
  memory_edit,   memory_pin, memory_archive

Hook primitives (also exposed as MCP for ease of access):
  memory_brief, memory_lint

Specialised:
  memory_invoke_skill — fetch full skill body_md (only surface that
    returns a full markdown body; all other tools return compact
    projections)
  memory_impact_check — pre-edit envelope: digest + callers + verdict
  memory_status — self-introspection: anchor / registry / counts
  memory_plan — list one plan's steps by task_id, rank-ordered

list / count / rollback / versions stay HTTP-only — accessible via
``memory-cli`` for ops, but not in the hot MCP path. Keeps the
agent's tool list small (single-screen).

Legacy v2 tool names (``memory_record_with_evidence``,
``memory_write_decision``, ``memory_ingest_episode``, etc.) are
served by ``mcp/v2_compat.py`` which translates them onto this
canonical backend — they no longer have their own implementation.
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
    "plan_step",
]


MEMORY_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_search",
        description=(
            "BM25 search with compact projections (~30 tokens per hit) and "
            "scores. Pass ``kinds`` to restrict to specific kinds; default = all. "
            "Set ``rerank=true`` to run the optional cross-encoder reranker "
            "(requires the [rerank] extra)."
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
        name="memory_get",
        description=(
            "Fetch one row by id. Default = compact projection (~30 tokens). "
            "Pass ``fields`` (CSV string OR list) to selectively fetch full "
            "columns like decision_text, rationale, body_md."
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
        name="memory_write",
        description=(
            "Create one row of the given kind. Computes the gist column on "
            "write (no on-the-fly summarization at read). Snapshots prior "
            "content to ``versions`` table. Returns the compact projection "
            "of the new row."
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
        name="memory_edit",
        description=(
            "Partial update on an existing row. Snapshots prior content to "
            "``versions`` table before write. Re-computes gist columns when "
            "source fields change. Returns the compact projection of the "
            "resulting row."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "kind": {"type": "string", "enum": _KINDS},
                "id": {"type": "string", "minLength": 1},
                "fields": {
                    "type": "object",
                    "description": "Column -> new-value map. Only listed columns are updated.",
                },
                "agent_id": {"type": "string", "default": "mcp"},
            },
            "required": ["kind", "id", "fields"],
        },
    ),
    types.Tool(
        name="memory_pin",
        description=(
            "Toggle pinned bit on a decision or behavior. Pinned rows ride "
            "every brief section regardless of recency or relevance. Pass "
            "``pinned=false`` to unpin."
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
        name="memory_archive",
        description=(
            "Mark a row as archived so it stops showing in brief / list / "
            "search by default but stays retrievable by id. Pass ``reason`` "
            "to record why."
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
        name="memory_brief",
        description=(
            "Session-start brief, capped at ``max_tokens``, composed from "
            "compact projections. Sections: identity, behaviors, decisions, "
            "state, code_hubs. Hook primitive for UserPromptSubmit / "
            "session-start injection. Pass ``session_id`` to opt into "
            "sticky-brief: subsequent calls in the same session shrink to "
            "``MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS`` (default 200)."
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
                "session_id": {"type": "string"},
            },
            "required": [],
        },
    ),
    types.Tool(
        name="memory_lint",
        description=(
            "Pre-task advisory. Returns {verdict, applicable_rules, "
            "related_decisions, prior_failures, watch_outs}. Hook primitive "
            "for PreToolUse wiring; safe to call advisory-only when there "
            "is no PreToolUse hook."
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
        name="memory_invoke_skill",
        description=(
            "Fetch full body_md of a skill and bump usage_count + "
            "last_invoked_at. The ONLY surface that returns full markdown "
            "body; every other tool returns compact projections."
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
    types.Tool(
        name="memory_impact_check",
        description=(
            "DISCIPLINE PRIMITIVE -- call this BEFORE reading or editing "
            "source files. Returns the one-shot impact envelope: digest "
            "(purpose + top symbols), callers (who depends on this file), "
            "hot symbols (>=3 callers each), verdict (low / medium / high "
            "/ not_indexed), and an advisory naming the right follow-up "
            "tool. Replaces a 3-call sequence (file_digest + "
            "graph_neighbors + ad-hoc Grep) with one cheap envelope. Use "
            "this INSTEAD of Read whenever the goal is impact analysis or "
            "symbol discovery; Read is fallback for understanding unknown "
            "algorithm logic."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "file_path": {"type": "string", "minLength": 1, "maxLength": 400},
                "callers_limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
                "hot_threshold": {
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["file_path"],
        },
    ),
    types.Tool(
        name="memory_status",
        description=(
            "Self-introspection: one-call diagnostic returning anchor / "
            "hub_mode / registry / current-workspace counts / adoption / "
            "applied_migrations. Closes the 'agent guesses its environment' "
            "footgun — call this on session start to verify which workspace "
            "the MCP server is bound to and how much data is in it. "
            "Set ``include_environment=false`` to drop the anchor/registry "
            "block (counts + adoption only). Set ``include_active_memory=true`` "
            "to add the v3.1 dashboard (open proposals + predictive warnings + "
            "blindspots + persisted-warnings counts)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "include_environment": {"type": "boolean", "default": True},
                "include_active_memory": {"type": "boolean", "default": False},
            },
            "required": [],
        },
    ),
    types.Tool(
        name="memory_plan",
        description=(
            "List the live steps of one plan (one task_id) as compact "
            "projections in rank order: id, title, status, rank, "
            "parent_step_id. The read companion to the plan_step write "
            "surface -- fetch the whole plan in one call instead of "
            "tracking step ids by hand. Re-planned-out (removed) steps "
            "are excluded."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "task_id": {"type": "string", "minLength": 1},
            },
            "required": ["task_id"],
        },
    ),
]
