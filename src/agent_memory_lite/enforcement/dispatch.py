"""Top-level PreToolUse dispatch.

Composition order:

  1. Load active enforcement-tagged rules for the workspace.
  2. Run the mechanical layer (regex + trail). If any block fires, we
     return immediately — the cheap layer must short-circuit so we
     never pay the Ollama cost on a tool call already disqualified by
     a deterministic check.
  3. If the mechanical layer passed AND any semantic rule exists, run
     the semantic layer (Ollama review).
  4. Collapse violations into a single ``HookDecision``.

Failure-soft: every layer is wrapped so a bug in one rule cannot crash
the hook and block every tool call indefinitely. On unexpected error
we return ``allow()`` and surface the error in stderr for the hook
script to log.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.enforcement.mechanical_dispatch import check_mechanical
from agent_memory_lite.enforcement.rule_loader import (
    EnforcementRule,
    filter_by_level,
    load_enforcement_rules,
)
from agent_memory_lite.enforcement.semantic_review import check_semantic
from agent_memory_lite.enforcement.verdict import HookDecision, allow, block


def decide(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    trail: list[str],
    ollama_base_url: str | None,
    ollama_model: str | None,
) -> HookDecision:
    """Decide whether to allow or block this tool call."""
    rules = load_enforcement_rules(conn, workspace_id)
    if not rules:
        return allow()

    mechanical_violations = check_mechanical(
        rules,
        tool_name=tool_name,
        tool_input=tool_input,
        trail=trail,
    )
    if mechanical_violations:
        # Cheap layer short-circuits — never pay Ollama cost when
        # mechanical already decided.
        return block(mechanical_violations)

    semantic_rules = filter_by_level(rules, "semantic")
    if not semantic_rules:
        return allow()
    if not ollama_base_url or not ollama_model:
        # Semantic layer requested but Ollama not configured — fail
        # OPEN so the hook does not become a deadlock when the
        # operator hasn't installed the LLM yet.
        return allow()

    semantic_violations = check_semantic(
        semantic_rules,
        tool_name=tool_name,
        tool_input=tool_input,
        trail=trail,
        base_url=ollama_base_url,
        model=ollama_model,
    )
    if semantic_violations:
        return block(semantic_violations)
    return allow()


def decide_with_rules(
    rules: list[EnforcementRule],
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    trail: list[str],
    ollama_base_url: str | None,
    ollama_model: str | None,
) -> HookDecision:
    """Same as ``decide``, but accepts pre-loaded rules (for tests)."""
    if not rules:
        return allow()
    mechanical_violations = check_mechanical(
        rules, tool_name=tool_name, tool_input=tool_input, trail=trail
    )
    if mechanical_violations:
        return block(mechanical_violations)
    semantic_rules = filter_by_level(rules, "semantic")
    if not semantic_rules or not ollama_base_url or not ollama_model:
        return allow()
    semantic_violations = check_semantic(
        semantic_rules,
        tool_name=tool_name,
        tool_input=tool_input,
        trail=trail,
        base_url=ollama_base_url,
        model=ollama_model,
    )
    if semantic_violations:
        return block(semantic_violations)
    return allow()
