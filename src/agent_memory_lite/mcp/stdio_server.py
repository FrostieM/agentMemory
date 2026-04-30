"""MCP stdio server.

Exposes the same service functions used by the HTTP routes as MCP tools so
Claude Code, Cursor, or any other MCP-aware agent can discover them as
first-class tool calls without needing the HTTP service to be up.

Run via:

    python -m agent_memory_lite.mcp.stdio_server

Register in Claude Code (`~/.claude/settings.json` or project
`.claude/settings.json`):

    {
      "mcpServers": {
        "agent-memory-lite": {
          "command": "python",
          "args": ["-m", "agent_memory_lite.mcp.stdio_server"],
          "env": {"OLLAMA_PROBE_SKIP": "true"}
        }
      }
    }

The server runs in-process for lightweight tools and owns one SQLite
connection. Retrieval and ingestion tools delegate to the local HTTP service
when possible so the MCP stdio process does not block while loading the
embedding model; if HTTP is unavailable, retrieval returns an in-process
FTS-only context and ingestion falls back to the local pipeline.
Multiple processes hitting the same DB are safe under SQLite WAL.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import mcp.server.stdio
from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.ingestion.candidate_writer import (
    promote_memory_candidate,
    reject_memory_candidate,
)
from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_role,
    upsert_agent_skill,
)
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.file_pipeline import ingest_file
from agent_memory_lite.ingestion.research_writer import (
    add_experiment_result,
    distill_insight,
    register_snapshot,
    update_insight,
    upsert_domain_concept,
    write_experiment,
)
from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.ingestion.theory_writer import add_theory_evidence, write_theory
from agent_memory_lite.logging_setup import configure_logging, get_logger
from agent_memory_lite.maintenance.usage_feedback import record_usage_feedback
from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.capabilities import AgentPlaybookIn, AgentRoleIn, AgentSkillIn
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.research import (
    DomainConceptIn,
    ExperimentIn,
    ExperimentResultIn,
    MemorySnapshotIn,
    ResearchInsightIn,
    ResearchInsightUpdateIn,
)
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.models.task_state import TaskStateIn
from agent_memory_lite.models.theories import TheoryEvidenceIn, TheoryIn
from agent_memory_lite.repositories.behavior_repo import list_behavior_instructions
from agent_memory_lite.repositories.candidates_repo import list_candidates
from agent_memory_lite.repositories.capabilities_repo import build_agent_capabilities
from agent_memory_lite.repositories.capability_links_repo import list_capability_links
from agent_memory_lite.repositories.decisions_repo import list_active_decisions, list_all_decisions
from agent_memory_lite.repositories.maintenance_repo import (
    list_maintenance_events,
    resolve_maintenance_event,
)
from agent_memory_lite.repositories.research_repo import (
    build_research_agenda,
    list_concepts,
    list_insights,
)
from agent_memory_lite.repositories.theories_repo import list_evidence_for_theory, list_theories
from agent_memory_lite.repositories.workspace_manifest_repo import ensure_workspace_manifest
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.utils.text_encoding import repair_common_mojibake
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.factory import get_vector_store
from agent_memory_lite.version import __version__

_log = get_logger("mcp.stdio_server")
_HTTP_GET_CONTEXT_DEFAULT_TIMEOUT_SECONDS = 2.0
_HTTP_WRITE_DEFAULT_TIMEOUT_SECONDS = 30.0


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, *, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _memory_http_base_url(settings: Settings) -> str:
    raw = os.environ.get("MEMORY_HTTP_BASE_URL") or os.environ.get("AGENT_MEMORY_HTTP_BASE_URL")
    if raw:
        return raw.rstrip("/")
    return f"http://127.0.0.1:{settings.api_port}"


def _resolve_paths_from_cwd(settings: Settings) -> Settings:
    """Override settings.db_path / settings.vector_db_path from the cwd.

    Precedence (highest first):
    1. Explicit env vars `MEMORY_DB_PATH` / `VECTOR_DB_PATH` (already
       baked into Settings via pydantic-settings).
    2. `<cwd>/.agent_memory/memory.db` if present — lets any runtime
       isolate per-project memory by spawning the MCP server in the
       project's working directory.
    3. Whatever the .env / built-in default produced.
    """
    if os.environ.get("MEMORY_DB_PATH"):
        return settings
    cwd = Path.cwd()
    candidate_db = cwd / ".agent_memory" / "memory.db"
    candidate_vec = cwd / ".agent_memory" / "vectors.lance"
    if not candidate_db.parent.exists():
        return settings
    return settings.model_copy(update={"db_path": candidate_db, "vector_db_path": candidate_vec})


class _Runtime:
    """Lazy holders for the per-process SQLite + provider + store."""

    def __init__(self) -> None:
        self.settings = _resolve_paths_from_cwd(get_settings())
        self.conn: sqlite3.Connection | None = None
        self._provider: EmbeddingProvider | None = None
        self._store: VectorStore | None = None

    def db(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = open_connection(self.settings.db_path)
            apply_migrations(self.conn)
            if self.settings.enforce_workspace_manifest:
                ensure_workspace_manifest(
                    self.conn,
                    workspace_id=self.settings.workspace_id,
                    allow_default_workspace=not self.settings.forbid_default_workspace,
                )
        return self.conn

    def provider(self) -> EmbeddingProvider:
        if self._provider is None:
            self._provider = get_embedding_provider(self.settings)
        return self._provider

    def store(self) -> VectorStore:
        if self._store is None:
            self._store = get_vector_store(self.settings)
        return self._store

    def close(self) -> None:
        if self.conn is not None:
            close_connection(self.conn)
            self.conn = None
        if self._store is not None:
            self._store.close()
            self._store = None


_runtime = _Runtime()
_server: Server = Server("agent-memory-lite")
_ListToolsHandler = Callable[[], Awaitable[list[types.Tool]]]
_ListToolsDecorator = Callable[[_ListToolsHandler], _ListToolsHandler]
_CallToolHandler = Callable[[str, dict[str, Any] | None], Awaitable[list[types.TextContent]]]
_CallToolDecorator = Callable[[_CallToolHandler], _CallToolHandler]
_list_tools_factory = cast(Callable[[], object], _server.list_tools)
_call_tool_factory = cast(Callable[[], object], _server.call_tool)
_list_tools_decorator = cast(_ListToolsDecorator, _list_tools_factory())
_call_tool_decorator = cast(_CallToolDecorator, _call_tool_factory())


def _workspace_schema() -> dict[str, str]:
    return {"type": "string", "default": _runtime.settings.workspace_id}


_TOOLS: list[types.Tool] = [
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
                "workspace_id": _workspace_schema(),
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
                "workspace_id": _workspace_schema(),
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
                "workspace_id": _workspace_schema(),
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
        name="memory_write_decision",
        description=(
            "Record an architectural decision. Pass supersedes_decision_id to "
            "close a prior decision atomically."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "title": {"type": "string", "minLength": 1},
                "decision_text": {"type": "string", "minLength": 1},
                "rationale": {"type": "string"},
                "supersedes_decision_id": {"type": "string"},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.9},
                "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.8},
            },
            "required": ["title", "decision_text"],
        },
    ),
    types.Tool(
        name="memory_list_decisions",
        description=(
            "Search active or historical architectural decisions by topic. Use this "
            "when you need a global view such as decisions about live execution."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "query": {"type": "string"},
                "include_superseded": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
            },
        },
    ),
    types.Tool(
        name="memory_update_task_state",
        description="Upsert task state keyed by (workspace_id, task_id).",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "task_id": {"type": "string", "minLength": 1},
                "goal": {"type": "string", "minLength": 1},
                "status": {"type": "string", "minLength": 1},
                "current_plan": {"type": "array", "items": {"type": "string"}},
                "completed_steps": {"type": "array", "items": {"type": "string"}},
                "next_action": {"type": "string"},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "files_in_scope": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
            },
            "required": ["task_id", "goal", "status"],
        },
    ),
    types.Tool(
        name="memory_ingest_file",
        description="Index a single file (idempotent by content_hash).",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
                "language": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    ),
    types.Tool(
        name="memory_list_candidates",
        description="List reviewable memory candidates created by extraction.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "query": {"type": "string"},
                "statuses": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    ),
    types.Tool(
        name="memory_promote_candidate",
        description="Promote a reviewed memory candidate into its explicit target table.",
        inputSchema={
            "type": "object",
            "properties": {"candidate_id": {"type": "string", "minLength": 1}},
            "required": ["candidate_id"],
        },
    ),
    types.Tool(
        name="memory_reject_candidate",
        description="Reject a memory candidate while preserving it for audit.",
        inputSchema={
            "type": "object",
            "properties": {"candidate_id": {"type": "string", "minLength": 1}},
            "required": ["candidate_id"],
        },
    ),
    types.Tool(
        name="memory_list_maintenance_events",
        description="List maintenance events that affect memory substrate trust.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "statuses": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
        },
    ),
    types.Tool(
        name="memory_resolve_maintenance_event",
        description="Mark a maintenance event resolved or ignored after review.",
        inputSchema={
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["resolved", "ignored"]},
            },
            "required": ["event_id"],
        },
    ),
    types.Tool(
        name="memory_link_capability",
        description=(
            "Link a role, skill, or playbook to a research object so it can "
            "influence hypothesis retrieval and context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "target_type": {"type": "string"},
                "target_id": {"type": "string", "minLength": 1},
                "capability_type": {"type": "string"},
                "capability_id": {"type": "string"},
                "capability_name": {"type": "string"},
                "relation": {"type": "string", "default": "method"},
                "rationale": {"type": "string"},
                "strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "source_episode_id": {"type": "string"},
            },
            "required": ["target_type", "target_id", "capability_type"],
        },
    ),
    types.Tool(
        name="memory_list_capability_links",
        description="List links from roles, skills, and playbooks to research memory objects.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "target_type": {"type": "string"},
                "target_id": {"type": "string"},
                "capability_type": {"type": "string"},
                "capability_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
    ),
    types.Tool(
        name="memory_upsert_behavior_instruction",
        description=(
            "Create or update a persistent behavior instruction with explicit "
            "kind, scope, priority, and conflict policy."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "name": {"type": "string", "minLength": 1},
                "rule": {"type": "string", "minLength": 1},
                "kind": {
                    "type": "string",
                    "enum": [
                        "communication_style",
                        "operating_rule",
                        "project_convention",
                        "workflow_preference",
                        "role_guidance",
                    ],
                    "default": "operating_rule",
                },
                "scope": {
                    "type": "string",
                    "enum": ["global", "workspace", "project", "task", "role"],
                    "default": "workspace",
                },
                "priority": {
                    "type": "string",
                    "enum": [
                        "system_bound",
                        "user_preference",
                        "project_convention",
                        "suggestion",
                    ],
                    "default": "user_preference",
                },
                "rationale": {"type": "string"},
                "applies_to": {"type": "array", "items": {"type": "string"}},
                "conflict_policy": {
                    "type": "string",
                    "enum": [
                        "system_wins",
                        "current_user_wins",
                        "higher_priority_wins",
                        "most_specific_wins",
                        "latest_wins",
                    ],
                    "default": "current_user_wins",
                },
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "active": {"type": "boolean"},
            },
            "required": ["name", "rule"],
        },
    ),
    types.Tool(
        name="memory_list_behavior_instructions",
        description="List persistent behavior instructions for agent communication and operating behavior.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "query": {"type": "string"},
                "kinds": {"type": "array", "items": {"type": "string"}},
                "include_inactive": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
        },
    ),
    types.Tool(
        name="memory_write_theory",
        description="Record a working research theory with claim, mechanism, predictions, and experiment plan.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "title": {"type": "string", "minLength": 1},
                "claim": {"type": "string", "minLength": 1},
                "domain": {"type": "string", "default": "general"},
                "mechanism": {"type": "string"},
                "predictions": {"type": "array", "items": {"type": "string"}},
                "validation_criteria": {"type": "array", "items": {"type": "string"}},
                "experiment_plan": {"type": "string"},
                "dependent_decision_ids": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string", "default": "proposed"},
                "supersedes_theory_id": {"type": "string"},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "required": ["title", "claim"],
        },
    ),
    types.Tool(
        name="memory_add_theory_evidence",
        description="Attach supporting, refuting, mixed, neutral, or experiment evidence to a theory.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "theory_id": {"type": "string", "minLength": 1},
                "kind": {"type": "string"},
                "summary": {"type": "string", "minLength": 1},
                "source_episode_id": {"type": "string"},
                "artifact_path": {"type": "string"},
                "metrics": {"type": "object"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "observed_at": {"type": "string"},
            },
            "required": ["theory_id", "kind", "summary"],
        },
    ),
    types.Tool(
        name="memory_list_theories",
        description="List relevant theory memory items, optionally including recent evidence.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "include_archived": {"type": "boolean", "default": False},
                "include_evidence": {"type": "boolean", "default": False},
                "evidence_limit": {"type": "integer", "minimum": 0, "maximum": 20, "default": 3},
            },
        },
    ),
    types.Tool(
        name="memory_register_snapshot",
        description="Register or update a research data snapshot with paths, build metadata, and table counts.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "snapshot_key": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "source": {"type": "string", "default": "manual"},
                "db_path": {"type": "string"},
                "duckdb_path": {"type": "string"},
                "parquet_dir": {"type": "string"},
                "window_start": {"type": "string"},
                "window_end": {"type": "string"},
                "build_sha": {"type": "string"},
                "build_branch": {"type": "string"},
                "build_time": {"type": "string"},
                "remote_host": {"type": "string"},
                "table_counts": {"type": "object"},
                "total_rows": {"type": "integer", "minimum": 0},
                "metadata": {"type": "object"},
                "source_episode_id": {"type": "string"},
            },
            "required": ["snapshot_key", "title"],
        },
    ),
    types.Tool(
        name="memory_write_experiment",
        description="Create a planned/running research experiment linked to a theory and/or data snapshot.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "theory_id": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "title": {"type": "string", "minLength": 1},
                "hypothesis": {"type": "string", "minLength": 1},
                "cohort_definition": {"type": "string"},
                "success_criteria": {"type": "object"},
                "command": {"type": "string"},
                "status": {"type": "string", "default": "planned"},
                "priority": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "owner": {"type": "string"},
                "due_at": {"type": "string"},
                "source_episode_id": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["title", "hypothesis"],
        },
    ),
    types.Tool(
        name="memory_add_experiment_result",
        description="Record an experiment result; linked theory confidence/status is updated automatically.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "experiment_id": {"type": "string", "minLength": 1},
                "theory_id": {"type": "string"},
                "kind": {"type": "string"},
                "summary": {"type": "string", "minLength": 1},
                "metrics": {"type": "object"},
                "artifact_path": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "observed_at": {"type": "string"},
                "source_episode_id": {"type": "string"},
            },
            "required": ["experiment_id", "kind", "summary"],
        },
    ),
    types.Tool(
        name="memory_upsert_concept",
        description="Create or update a domain concept so research vocabulary is explicit and reusable.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "name": {"type": "string", "minLength": 1},
                "kind": {"type": "string", "default": "term"},
                "definition": {"type": "string", "minLength": 1},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "active": {"type": "boolean"},
            },
            "required": ["name", "definition"],
        },
    ),
    types.Tool(
        name="memory_distill_insight",
        description="Promote raw episode learnings into actionable insights or open questions.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "insight_type": {"type": "string"},
                "summary": {"type": "string", "minLength": 1},
                "proposed_action": {"type": "string"},
                "target_type": {"type": "string"},
                "target_id": {"type": "string"},
                "source_episode_ids": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "status": {"type": "string", "default": "new"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["insight_type", "summary"],
        },
    ),
    types.Tool(
        name="memory_update_insight",
        description="Update an existing research insight's target link or status.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "insight_id": {"type": "string", "minLength": 1},
                "target_type": {"type": "string", "minLength": 1},
                "target_id": {"type": "string", "minLength": 1},
                "status": {"type": "string"},
                "source_episode_id": {"type": "string"},
            },
            "required": ["insight_id"],
        },
    ),
    types.Tool(
        name="memory_list_research_agenda",
        description="List current snapshots, open experiments, insights, and concepts relevant to a query.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
        },
    ),
    types.Tool(
        name="memory_list_concepts",
        description="List domain concepts in the project memory.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "query": {"type": "string"},
                "include_inactive": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    ),
    types.Tool(
        name="memory_list_insights",
        description="List distilled research insights and open questions.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    ),
    types.Tool(
        name="memory_upsert_agent_role",
        description="Create or update a first-class agent role with responsibilities and boundaries.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "name": {"type": "string", "minLength": 1},
                "purpose": {"type": "string", "minLength": 1},
                "responsibilities": {"type": "array", "items": {"type": "string"}},
                "boundaries": {"type": "array", "items": {"type": "string"}},
                "handoff_triggers": {"type": "array", "items": {"type": "string"}},
                "tools": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "active": {"type": "boolean"},
            },
            "required": ["name", "purpose"],
        },
    ),
    types.Tool(
        name="memory_upsert_agent_skill",
        description="Create or update a reusable agent skill with inputs, outputs, and related roles.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "name": {"type": "string", "minLength": 1},
                "summary": {"type": "string", "minLength": 1},
                "when_to_use": {"type": "array", "items": {"type": "string"}},
                "inputs": {"type": "array", "items": {"type": "string"}},
                "outputs": {"type": "array", "items": {"type": "string"}},
                "tools": {"type": "array", "items": {"type": "string"}},
                "related_roles": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "active": {"type": "boolean"},
            },
            "required": ["name", "summary"],
        },
    ),
    types.Tool(
        name="memory_upsert_agent_playbook",
        description="Create or update a repeatable agent playbook with triggers, steps, and success criteria.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "name": {"type": "string", "minLength": 1},
                "goal": {"type": "string", "minLength": 1},
                "triggers": {"type": "array", "items": {"type": "string"}},
                "steps": {"type": "array", "items": {"type": "string"}},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "required_skills": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "active": {"type": "boolean"},
            },
            "required": ["name", "goal"],
        },
    ),
    types.Tool(
        name="memory_list_agent_capabilities",
        description="List relevant roles, skills, and playbooks for a query.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "query": {"type": "string"},
                "include_inactive": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 6},
            },
        },
    ),
    types.Tool(
        name="memory_record_usage_feedback",
        description=(
            "Record whether a retrieved memory item was helpful or noisy so "
            "future ranking can improve."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": _workspace_schema(),
                "source_type": {
                    "type": "string",
                    "enum": ["chunk", "decision", "theory", "insight", "capability"],
                    "default": "chunk",
                },
                "source_id": {"type": "string", "minLength": 1},
                "query": {"type": "string"},
                "usefulness": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                "task_id": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["source_id", "usefulness"],
        },
    ),
]


@_list_tools_decorator
async def _list_tools() -> list[types.Tool]:
    return _TOOLS


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _with_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = _drop_none(payload)
    cleaned.setdefault("workspace_id", _runtime.settings.workspace_id)
    _ensure_workspace_allowed(str(cleaned["workspace_id"]))
    return cleaned


def _ensure_workspace_allowed(workspace_id: str) -> None:
    if _runtime.settings.forbid_default_workspace and workspace_id == "default":
        raise ValueError("workspace_id='default' is disabled by MEMORY_FORBID_DEFAULT_WORKSPACE")


def _workspace_from_args(args: dict[str, Any]) -> str:
    workspace_id = str(args.get("workspace_id", _runtime.settings.workspace_id))
    _ensure_workspace_allowed(workspace_id)
    return workspace_id


def _http_memory_request(
    *,
    path: str,
    payload: dict[str, Any],
    enabled_env: str,
    timeout_env: str,
    default_timeout: float,
    log_label: str,
) -> dict[str, Any] | None:
    """Delegate a memory call to the warm local HTTP service when possible."""

    if not _env_flag(enabled_env, default=True):
        return None

    base_url = _memory_http_base_url(_runtime.settings)
    timeout = _env_float(timeout_env, default=default_timeout)
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Memory-DB-Path": str(_runtime.settings.db_path),
            "X-Memory-Vector-Path": str(_runtime.settings.vector_db_path),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body: Any = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        _log.warning("MCP %s HTTP delegation failed; using local fallback: %s", log_label, exc)
        return None

    if not isinstance(raw_body, dict):
        _log.warning("MCP %s HTTP delegation returned an unexpected payload", log_label)
        return None
    return raw_body


def _http_get_context(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Delegate MCP context retrieval to the warm local HTTP service when possible.

    The stdio MCP process is frequently short-lived. Loading the embedding model
    inside that process can block tool calls for minutes, while the local HTTP
    service already owns a warmed provider/vector store. If HTTP is unavailable,
    callers fall back to an in-process FTS-only context below.
    """

    body = _http_memory_request(
        path="/memory/get_context",
        payload=payload,
        enabled_env="MCP_GET_CONTEXT_HTTP_DELEGATE",
        timeout_env="MCP_GET_CONTEXT_HTTP_TIMEOUT_SEC",
        default_timeout=_HTTP_GET_CONTEXT_DEFAULT_TIMEOUT_SECONDS,
        log_label="get_context",
    )
    if body is None:
        return None
    if not isinstance(body.get("context_text"), str):
        _log.warning("MCP get_context HTTP delegation returned an unexpected payload")
        return None
    sources = body.get("sources")
    if not isinstance(sources, list):
        body["sources"] = []
    return body


def _http_ingest_episode(payload: dict[str, Any]) -> dict[str, Any] | None:
    body = _http_memory_request(
        path="/memory/ingest_episode",
        payload=payload,
        enabled_env="MCP_INGEST_HTTP_DELEGATE",
        timeout_env="MCP_INGEST_HTTP_TIMEOUT_SEC",
        default_timeout=_HTTP_WRITE_DEFAULT_TIMEOUT_SECONDS,
        log_label="ingest_episode",
    )
    if body is not None and {"episode_id", "chunk_id"}.issubset(body):
        return body
    return None


def _http_ingest_file(payload: dict[str, Any]) -> dict[str, Any] | None:
    body = _http_memory_request(
        path="/memory/ingest_file",
        payload=payload,
        enabled_env="MCP_INGEST_HTTP_DELEGATE",
        timeout_env="MCP_INGEST_HTTP_TIMEOUT_SEC",
        default_timeout=_HTTP_WRITE_DEFAULT_TIMEOUT_SECONDS,
        log_label="ingest_file",
    )
    if body is not None and {"file_id", "path"}.issubset(body):
        return body
    return None


def _handle_get_context(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_get_context(payload)
    if delegated is not None:
        return delegated

    query = RetrievalQuery(**payload)
    built = build_context(
        _runtime.db(),
        query,
        embedding_provider=None,
        vector_store=None,
    )
    return {
        "context_text": built.text,
        "sources": [
            {
                "id": hit.id,
                "score": hit.score,
                "sources": hit.sources,
                "path": hit.path,
            }
            for hit in built.hits
        ],
    }


def _handle_search(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args)
    query = args["query"]
    limit = int(args.get("limit", 10))
    hits = search_chunks_fts(
        _runtime.db(),
        workspace_id=workspace_id,
        query=query,
        limit=limit,
    )
    return {
        "mode": "fts",
        "hits": [
            {
                "chunk_id": hit.chunk_id,
                "score": hit.score,
                "path": hit.path,
                "text": hit.text,
                "summary": hit.summary,
            }
            for hit in hits
        ],
    }


def _handle_ingest_episode(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    payload.setdefault("source_type", "agent_action")
    payload.setdefault("trust_level", "agent_observed")
    delegated = _http_ingest_episode(payload)
    if delegated is not None:
        return delegated

    result = ingest_episode(
        _runtime.db(),
        EpisodeIn(**payload),
        embedding_provider=_runtime.provider(),
        vector_store=_runtime.store(),
        auto_promote_settings=_runtime.settings,
    )
    return {
        "episode_id": result.episode.id,
        "chunk_id": result.chunk.id,
        "redacted_text": result.episode.raw_text,
        "redacted_kinds": result.redacted_kinds,
        "embedded": result.embedded,
        "auto_promoted_decisions": result.auto_promoted_decisions,
        "auto_promoted_rules": result.auto_promoted_rules,
        "auto_promoted_core": result.auto_promoted_core,
        "candidates_written": result.candidates_written,
    }


def _handle_write_decision(args: dict[str, Any]) -> dict[str, Any]:
    decision = write_decision(_runtime.db(), DecisionIn(**_with_workspace(args)))
    return {
        "decision_id": decision.id,
        "status": decision.status.value,
        "valid_from": decision.valid_from,
        "superseded_decision_id": decision.supersedes_decision_id,
    }


def _decision_payload(item: Any) -> dict[str, Any]:
    return {
        "decision_id": item.id,
        "title": repair_common_mojibake(item.title),
        "decision_text": repair_common_mojibake(item.decision_text),
        "rationale": repair_common_mojibake(item.rationale) if item.rationale else None,
        "status": item.status.value,
        "supersedes_decision_id": item.supersedes_decision_id,
        "source_episode_id": item.source_episode_id,
        "confidence": item.confidence,
        "importance": item.importance,
        "valid_from": item.valid_from,
        "valid_to": item.valid_to,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _handle_list_decisions(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args)
    query = args.get("query")
    include_superseded = bool(args.get("include_superseded", False))
    limit = int(args.get("limit", 10))
    if include_superseded:
        decisions = list_all_decisions(
            _runtime.db(),
            workspace_id,
            query=str(query) if query is not None else None,
            limit=limit,
        )
    else:
        decisions = list_active_decisions(
            _runtime.db(),
            workspace_id,
            query=str(query) if query is not None else None,
            limit=limit,
        )
    return {"decisions": [_decision_payload(item) for item in decisions]}


def _handle_update_task_state(args: dict[str, Any]) -> dict[str, Any]:
    state = write_task_state(_runtime.db(), TaskStateIn(**_with_workspace(args)))
    return {
        "state_id": state.id,
        "task_id": state.task_id,
        "status": state.status,
        "updated_at": state.updated_at,
    }


def _handle_ingest_file(args: dict[str, Any]) -> dict[str, Any]:
    payload = _drop_none(args)
    workspace_id = str(payload.pop("workspace_id", _runtime.settings.workspace_id))
    _ensure_workspace_allowed(workspace_id)
    payload["workspace_id"] = workspace_id
    delegated = _http_ingest_file(payload)
    if delegated is not None:
        return delegated

    result = ingest_file(
        _runtime.db(),
        workspace_id=workspace_id,
        embedding_provider=_runtime.provider(),
        vector_store=_runtime.store(),
        **payload,
    )
    return {
        "file_id": result.file.id,
        "path": result.file.path,
        "chunks_written": result.chunks_written,
        "skipped": result.skipped,
        "last_indexed_at": result.file.last_indexed_at,
    }


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    return {
        "candidate_id": candidate.id,
        "workspace_id": candidate.workspace_id,
        "kind": candidate.kind.value,
        "subject": candidate.subject,
        "predicate": candidate.predicate,
        "object": candidate.object,
        "evidence": candidate.evidence,
        "confidence": candidate.confidence,
        "importance": candidate.importance,
        "trust_level": candidate.trust_level.value,
        "source_episode_id": candidate.source_episode_id,
        "status": candidate.status.value,
        "promoted_target_type": candidate.promoted_target_type,
        "promoted_target_id": candidate.promoted_target_id,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
        "decided_at": candidate.decided_at,
    }


def _capability_link_payload(link: Any) -> dict[str, Any]:
    return {
        "link_id": link.id,
        "workspace_id": link.workspace_id,
        "target_type": link.target_type.value,
        "target_id": link.target_id,
        "capability_type": link.capability_type.value,
        "capability_id": link.capability_id,
        "capability_name": link.capability_name,
        "relation": link.relation.value,
        "rationale": link.rationale,
        "strength": link.strength,
        "source_episode_id": link.source_episode_id,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }


def _maintenance_event_payload(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "workspace_id": event.workspace_id,
        "kind": event.kind,
        "severity": event.severity.value,
        "status": event.status.value,
        "summary": event.summary,
        "details": event.details,
        "source_episode_id": event.source_episode_id,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "created_at": event.created_at,
        "resolved_at": event.resolved_at,
    }


def _behavior_instruction_payload(item: Any) -> dict[str, Any]:
    return {
        "instruction_id": item.id,
        "workspace_id": item.workspace_id,
        "name": item.name,
        "kind": item.kind.value,
        "scope": item.scope.value,
        "priority": item.priority.value,
        "rule": item.rule,
        "rationale": item.rationale,
        "applies_to": item.applies_to,
        "conflict_policy": item.conflict_policy.value,
        "source_episode_id": item.source_episode_id,
        "confidence": item.confidence,
        "active": item.active,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _handle_list_candidates(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MemoryCandidateStatus  # noqa: PLC0415

    workspace_id = _workspace_from_args(args)
    raw_statuses = args.get("statuses")
    statuses = [MemoryCandidateStatus(item) for item in raw_statuses] if raw_statuses else None
    return {
        "candidates": [
            _candidate_payload(candidate)
            for candidate in list_candidates(
                _runtime.db(),
                workspace_id=workspace_id,
                query=args.get("query"),
                statuses=statuses,
                limit=int(args.get("limit", 20)),
            )
        ]
    }


def _handle_promote_candidate(args: dict[str, Any]) -> dict[str, Any]:
    return _candidate_payload(
        promote_memory_candidate(_runtime.db(), candidate_id=str(args["candidate_id"]))
    )


def _handle_reject_candidate(args: dict[str, Any]) -> dict[str, Any]:
    return _candidate_payload(
        reject_memory_candidate(_runtime.db(), candidate_id=str(args["candidate_id"]))
    )


def _handle_list_maintenance_events(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MaintenanceEventStatus  # noqa: PLC0415

    workspace_id = _workspace_from_args(args)
    raw_statuses = args.get("statuses")
    statuses = [MaintenanceEventStatus(item) for item in raw_statuses] if raw_statuses else None
    return {
        "events": [
            _maintenance_event_payload(event)
            for event in list_maintenance_events(
                _runtime.db(),
                workspace_id=workspace_id,
                statuses=statuses,
                limit=int(args.get("limit", 20)),
            )
        ]
    }


def _handle_resolve_maintenance_event(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MaintenanceEventStatus  # noqa: PLC0415

    status = MaintenanceEventStatus(args.get("status", "resolved"))
    event = resolve_maintenance_event(
        _runtime.db(),
        event_id=str(args["event_id"]),
        status=status,
        resolved_at=iso_now(),
    )
    if event is None:
        raise ValueError(f"maintenance event not found: {args['event_id']}")
    return _maintenance_event_payload(event)


def _handle_link_capability(args: dict[str, Any]) -> dict[str, Any]:
    return _capability_link_payload(
        link_capability(_runtime.db(), CapabilityLinkIn(**_with_workspace(args)))
    )


def _handle_list_capability_links(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.models.enums import (  # noqa: PLC0415
        CapabilityLinkTargetType,
        CapabilityType,
    )

    workspace_id = _workspace_from_args(args)
    return {
        "links": [
            _capability_link_payload(link)
            for link in list_capability_links(
                _runtime.db(),
                workspace_id=workspace_id,
                target_type=(
                    CapabilityLinkTargetType(args["target_type"])
                    if args.get("target_type")
                    else None
                ),
                target_id=args.get("target_id"),
                capability_type=(
                    CapabilityType(args["capability_type"]) if args.get("capability_type") else None
                ),
                capability_id=args.get("capability_id"),
                limit=int(args.get("limit", 50)),
            )
        ]
    }


def _handle_upsert_behavior_instruction(args: dict[str, Any]) -> dict[str, Any]:
    instruction = upsert_behavior_instruction(
        _runtime.db(),
        BehaviorInstructionIn(**_with_workspace(args)),
    )
    return _behavior_instruction_payload(instruction)


def _handle_list_behavior_instructions(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.models.enums import BehaviorInstructionKind  # noqa: PLC0415

    workspace_id = _workspace_from_args(args)
    raw_kinds = args.get("kinds")
    kinds = [BehaviorInstructionKind(item) for item in raw_kinds] if raw_kinds else None
    return {
        "instructions": [
            _behavior_instruction_payload(item)
            for item in list_behavior_instructions(
                _runtime.db(),
                workspace_id=workspace_id,
                query=args.get("query"),
                kinds=kinds,
                include_inactive=bool(args.get("include_inactive", False)),
                limit=int(args.get("limit", 10)),
            )
        ]
    }


def _handle_write_theory(args: dict[str, Any]) -> dict[str, Any]:
    theory = write_theory(_runtime.db(), TheoryIn(**_with_workspace(args)))
    return {
        "theory_id": theory.id,
        "status": theory.status.value,
        "confidence": theory.confidence,
        "importance": theory.importance,
        "evidence_count": theory.evidence_count,
        "evidence_strength": theory.evidence_strength,
    }


def _handle_add_theory_evidence(args: dict[str, Any]) -> dict[str, Any]:
    evidence = add_theory_evidence(_runtime.db(), TheoryEvidenceIn(**_with_workspace(args)))
    return {
        "evidence_id": evidence.id,
        "theory_id": evidence.theory_id,
        "kind": evidence.kind.value,
        "observed_at": evidence.observed_at,
    }


def _handle_list_theories(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args)
    include_evidence = bool(args.get("include_evidence", False))
    evidence_limit = int(args.get("evidence_limit", 3))
    theories = list_theories(
        _runtime.db(),
        workspace_id=workspace_id,
        query=args.get("query"),
        limit=int(args.get("limit", 20)),
        include_archived=bool(args.get("include_archived", False)),
    )
    return {
        "theories": [
            {
                "theory_id": theory.id,
                "title": theory.title,
                "domain": theory.domain,
                "claim": theory.claim,
                "validation_criteria": theory.validation_criteria,
                "dependent_decision_ids": theory.dependent_decision_ids,
                "status": theory.status.value,
                "confidence": theory.confidence,
                "importance": theory.importance,
                "evidence_count": theory.evidence_count,
                "evidence_strength": theory.evidence_strength,
                "tags": theory.tags,
                "evidence": [
                    {
                        "evidence_id": evidence.id,
                        "kind": evidence.kind.value,
                        "summary": evidence.summary,
                        "confidence": evidence.confidence,
                        "observed_at": evidence.observed_at,
                    }
                    for evidence in (
                        list_evidence_for_theory(_runtime.db(), theory.id, limit=evidence_limit)
                        if include_evidence
                        else []
                    )
                ],
            }
            for theory in theories
        ],
    }


def _handle_register_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    snapshot = register_snapshot(_runtime.db(), MemorySnapshotIn(**_with_workspace(args)))
    return {
        "snapshot_id": snapshot.id,
        "snapshot_key": snapshot.snapshot_key,
        "total_rows": snapshot.total_rows,
        "duckdb_path": snapshot.duckdb_path,
        "updated_at": snapshot.updated_at,
    }


def _handle_write_experiment(args: dict[str, Any]) -> dict[str, Any]:
    experiment = write_experiment(_runtime.db(), ExperimentIn(**_with_workspace(args)))
    return {
        "experiment_id": experiment.id,
        "theory_id": experiment.theory_id,
        "snapshot_id": experiment.snapshot_id,
        "status": experiment.status.value,
        "priority": experiment.priority,
    }


def _handle_add_experiment_result(args: dict[str, Any]) -> dict[str, Any]:
    result = add_experiment_result(_runtime.db(), ExperimentResultIn(**_with_workspace(args)))
    return {
        "result_id": result.id,
        "experiment_id": result.experiment_id,
        "theory_id": result.theory_id,
        "kind": result.kind.value,
        "observed_at": result.observed_at,
    }


def _handle_upsert_concept(args: dict[str, Any]) -> dict[str, Any]:
    concept = upsert_domain_concept(_runtime.db(), DomainConceptIn(**_with_workspace(args)))
    return {
        "concept_id": concept.id,
        "name": concept.name,
        "kind": concept.kind.value,
        "confidence": concept.confidence,
        "active": concept.active,
    }


def _handle_distill_insight(args: dict[str, Any]) -> dict[str, Any]:
    insight = distill_insight(_runtime.db(), ResearchInsightIn(**_with_workspace(args)))
    return {
        "insight_id": insight.id,
        "insight_type": insight.insight_type.value,
        "status": insight.status.value,
        "target_type": insight.target_type,
        "target_id": insight.target_id,
    }


def _handle_update_insight(args: dict[str, Any]) -> dict[str, Any]:
    insight = update_insight(_runtime.db(), ResearchInsightUpdateIn(**_with_workspace(args)))
    return {
        "insight_id": insight.id,
        "insight_type": insight.insight_type.value,
        "status": insight.status.value,
        "target_type": insight.target_type,
        "target_id": insight.target_id,
        "updated_at": insight.updated_at,
    }


def _handle_list_research_agenda(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args)
    agenda = build_research_agenda(
        _runtime.db(),
        workspace_id=workspace_id,
        query=args.get("query"),
        limit=int(args.get("limit", 10)),
    )
    return {
        "snapshots": [
            {
                "snapshot_id": item.id,
                "snapshot_key": item.snapshot_key,
                "title": item.title,
                "total_rows": item.total_rows,
                "duckdb_path": item.duckdb_path,
            }
            for item in agenda.snapshots
        ],
        "experiments": [
            {
                "experiment_id": item.id,
                "title": item.title,
                "theory_id": item.theory_id,
                "snapshot_id": item.snapshot_id,
                "status": item.status.value,
                "priority": item.priority,
                "hypothesis": item.hypothesis,
            }
            for item in agenda.experiments
        ],
        "insights": [
            {
                "insight_id": item.id,
                "insight_type": item.insight_type.value,
                "summary": item.summary,
                "status": item.status.value,
                "confidence": item.confidence,
                "target_type": item.target_type,
                "target_id": item.target_id,
            }
            for item in agenda.insights
        ],
        "concepts": [
            {
                "concept_id": item.id,
                "name": item.name,
                "kind": item.kind.value,
                "definition": item.definition,
                "confidence": item.confidence,
            }
            for item in agenda.concepts
        ],
    }


def _handle_list_concepts(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args)
    concepts = list_concepts(
        _runtime.db(),
        workspace_id=workspace_id,
        query=args.get("query"),
        include_inactive=bool(args.get("include_inactive", False)),
        limit=int(args.get("limit", 20)),
    )
    return {
        "concepts": [
            {
                "concept_id": item.id,
                "name": item.name,
                "kind": item.kind.value,
                "definition": item.definition,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in concepts
        ],
    }


def _handle_list_insights(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args)
    insights = list_insights(
        _runtime.db(),
        workspace_id=workspace_id,
        query=args.get("query"),
        limit=int(args.get("limit", 20)),
    )
    return {
        "insights": [
            {
                "insight_id": item.id,
                "insight_type": item.insight_type.value,
                "summary": item.summary,
                "status": item.status.value,
                "confidence": item.confidence,
                "target_type": item.target_type,
                "target_id": item.target_id,
            }
            for item in insights
        ],
    }


def _handle_upsert_agent_role(args: dict[str, Any]) -> dict[str, Any]:
    role = upsert_agent_role(_runtime.db(), AgentRoleIn(**_with_workspace(args)))
    return {
        "role_id": role.id,
        "name": role.name,
        "confidence": role.confidence,
        "active": role.active,
        "updated_at": role.updated_at,
    }


def _handle_upsert_agent_skill(args: dict[str, Any]) -> dict[str, Any]:
    skill = upsert_agent_skill(_runtime.db(), AgentSkillIn(**_with_workspace(args)))
    return {
        "skill_id": skill.id,
        "name": skill.name,
        "confidence": skill.confidence,
        "active": skill.active,
        "updated_at": skill.updated_at,
    }


def _handle_upsert_agent_playbook(args: dict[str, Any]) -> dict[str, Any]:
    playbook = upsert_agent_playbook(_runtime.db(), AgentPlaybookIn(**_with_workspace(args)))
    return {
        "playbook_id": playbook.id,
        "name": playbook.name,
        "confidence": playbook.confidence,
        "active": playbook.active,
        "updated_at": playbook.updated_at,
    }


def _handle_list_agent_capabilities(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args)
    capabilities = build_agent_capabilities(
        _runtime.db(),
        workspace_id=workspace_id,
        query=args.get("query"),
        include_inactive=bool(args.get("include_inactive", False)),
        limit=int(args.get("limit", 6)),
    )
    return {
        "roles": [
            {
                "role_id": item.id,
                "name": item.name,
                "purpose": item.purpose,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in capabilities.roles
        ],
        "skills": [
            {
                "skill_id": item.id,
                "name": item.name,
                "summary": item.summary,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in capabilities.skills
        ],
        "playbooks": [
            {
                "playbook_id": item.id,
                "name": item.name,
                "goal": item.goal,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in capabilities.playbooks
        ],
    }


def _handle_record_usage_feedback(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    feedback = record_usage_feedback(
        _runtime.db(),
        workspace_id=str(payload["workspace_id"]),
        source_type=str(payload.get("source_type", "chunk")),
        source_id=str(payload["source_id"]),
        query=str(payload.get("query", "")),
        usefulness=float(payload["usefulness"]),
        task_id=payload.get("task_id"),
        notes=str(payload.get("notes", "")),
    )
    return feedback.to_dict()


_HANDLERS = {
    "memory_get_context": _handle_get_context,
    "memory_search": _handle_search,
    "memory_ingest_episode": _handle_ingest_episode,
    "memory_write_decision": _handle_write_decision,
    "memory_list_decisions": _handle_list_decisions,
    "memory_update_task_state": _handle_update_task_state,
    "memory_ingest_file": _handle_ingest_file,
    "memory_list_candidates": _handle_list_candidates,
    "memory_promote_candidate": _handle_promote_candidate,
    "memory_reject_candidate": _handle_reject_candidate,
    "memory_list_maintenance_events": _handle_list_maintenance_events,
    "memory_resolve_maintenance_event": _handle_resolve_maintenance_event,
    "memory_link_capability": _handle_link_capability,
    "memory_list_capability_links": _handle_list_capability_links,
    "memory_upsert_behavior_instruction": _handle_upsert_behavior_instruction,
    "memory_list_behavior_instructions": _handle_list_behavior_instructions,
    "memory_write_theory": _handle_write_theory,
    "memory_add_theory_evidence": _handle_add_theory_evidence,
    "memory_list_theories": _handle_list_theories,
    "memory_register_snapshot": _handle_register_snapshot,
    "memory_write_experiment": _handle_write_experiment,
    "memory_add_experiment_result": _handle_add_experiment_result,
    "memory_upsert_concept": _handle_upsert_concept,
    "memory_distill_insight": _handle_distill_insight,
    "memory_update_insight": _handle_update_insight,
    "memory_list_research_agenda": _handle_list_research_agenda,
    "memory_list_concepts": _handle_list_concepts,
    "memory_list_insights": _handle_list_insights,
    "memory_upsert_agent_role": _handle_upsert_agent_role,
    "memory_upsert_agent_skill": _handle_upsert_agent_skill,
    "memory_upsert_agent_playbook": _handle_upsert_agent_playbook,
    "memory_list_agent_capabilities": _handle_list_agent_capabilities,
    "memory_record_usage_feedback": _handle_record_usage_feedback,
}


@_call_tool_decorator
async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    if name not in _HANDLERS:
        return [types.TextContent(type="text", text=json.dumps({"error": f"unknown tool: {name}"}))]
    args = arguments or {}
    try:
        result = await asyncio.to_thread(_HANDLERS[name], args)
    except Exception as exc:
        _log.error("mcp_tool_error", tool=name, error=str(exc))
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
            )
        ]
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


async def _run() -> None:
    settings = _runtime.settings
    configure_logging(settings.log_level)
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await _server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="agent-memory-lite",
                server_version=__version__,
                capabilities=_server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> int:
    try:
        asyncio.run(_run())
    finally:
        _runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
