"""Request-scoped agent identity (1.3.0).

The HTTP service runs in process-shared FastAPI workers. To attribute
audit-log rows to the agent that produced them (Claude vs Codex vs
background scripts vs MCP-hub), we use a ContextVar set by middleware
on each incoming request from the ``X-Memory-Agent-Id`` header.

``insert_audit`` reads ``current_agent_id()`` and stores it on the
audit row (NULL when the header is absent — matches pre-1.3.0 rows).

Conventions for X-Memory-Agent-Id values (operator may choose any):
* ``claude-code`` — Claude Code IDE / desktop
* ``codex`` — Codex CLI
* ``mcp-hub`` — shared MCP stdio server in hub mode
* ``script:<scriptname>`` — operator-side scripts (e.g. ``script:hygiene``)
* ``test`` — automated tests
* ``unknown`` — unidentified (also the default when header is missing)

The header is informational, never authoritative. Strict-isolation
guards in ``deps.py`` ignore agent_id; operator workspace permissions
are unchanged. This is purely for telemetry partitioning.
"""

from __future__ import annotations

from contextvars import ContextVar

_current_agent_id: ContextVar[str | None] = ContextVar("agent_memory_lite_agent_id", default=None)


def set_current_agent_id(agent_id: str | None) -> None:
    """Set the current request's agent identity. Called by middleware
    once per HTTP request from the ``X-Memory-Agent-Id`` header."""
    _current_agent_id.set(agent_id)


def current_agent_id() -> str | None:
    """Return the request-scoped agent identity, or ``None`` when no
    middleware set it (offline scripts, tests without the middleware
    fixture, MCP stdio direct calls)."""
    return _current_agent_id.get()


def reset_current_agent_id() -> None:
    """Clear the agent identity. Used by middleware in a finally-block
    to ensure context isolation across requests in single-threaded
    test clients."""
    _current_agent_id.set(None)
