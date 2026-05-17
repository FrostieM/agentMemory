"""memory_lint — pre-task active priming.

Called by the PreToolUse hook BEFORE a tool call executes. Wraps the
existing ``enforcement/dispatch.py`` mechanical+semantic stack and
enriches the verdict with:

* applicable_rules — pinned behaviors scoped to this tool
* related_decisions — top-3 compact projections matching the payload
* prior_failures — recent rejected theories or correction episodes
* watch_outs — single distilled hint when there's a real adoption-gap concern

Verdict map:
  HookDecision.allow=True, 0 violations → verdict="allow"
  HookDecision.allow=True, advisory     → verdict="allow_with_advisories"
  HookDecision.allow=False              → verdict="block"

Failure-soft: any subsystem error degrades the verdict to "allow" with
a diagnostic line; the hook never fails on a lint subsystem crash.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.enforcement.dispatch import decide_with_rules
from agent_memory_lite.enforcement.rule_loader import (
    EnforcementRule,
    load_enforcement_rules,
)
from agent_memory_lite.enforcement.session_trail import read_prior_tool_calls
from agent_memory_lite.v3.storage.reader import search

# ============================================================
# Result type
# ============================================================


@dataclass(frozen=True, slots=True)
class LintResult:
    """Pre-task lint payload returned to the hook script."""

    verdict: str  # 'allow' | 'allow_with_advisories' | 'block'
    applicable_rules: list[dict[str, Any]] = field(default_factory=list)
    related_decisions: list[dict[str, Any]] = field(default_factory=list)
    prior_failures: list[dict[str, Any]] = field(default_factory=list)
    watch_outs: str = ""
    diagnostic: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "applicable_rules": list(self.applicable_rules),
            "related_decisions": list(self.related_decisions),
            "prior_failures": list(self.prior_failures),
            "watch_outs": self.watch_outs,
            "diagnostic": self.diagnostic,
        }


# ============================================================
# Helpers
# ============================================================


def _payload_query(tool_payload: dict[str, Any]) -> str:
    """Extract a meaningful search token from common tool payload fields.

    Returns ONE compact token suitable for a LIKE search. Examples:
      file_path="src/strategy/kelly.py" → "kelly"
      command="deploy step run"        → "deploy"
      title="Adopt v3"                 → "Adopt v3"
    """
    file_path = tool_payload.get("file_path")
    if isinstance(file_path, str) and file_path.strip():
        # Take basename, strip extension.
        name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
        stem = name.split(".", 1)[0]
        if stem:
            return stem
    command = tool_payload.get("command")
    if isinstance(command, str) and command.strip():
        # Take first word of the command.
        first = command.strip().split(None, 1)[0]
        if first:
            return first
    for key in ("title", "decision_text", "name", "query"):
        value = tool_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _rule_to_advisory(rule: EnforcementRule) -> dict[str, Any]:
    """Compact projection for an applicable rule."""
    return {
        "rule_id": rule.id,
        "rule_name": rule.name,
        "level": rule.level,
        "advisory": rule.rule[:200],
    }


def _related_decisions(
    conn: sqlite3.Connection, workspace_id: str, query: str, limit: int = 3
) -> list[dict[str, Any]]:
    """Top-N compact decision projections matching the payload query."""
    if not query:
        return []
    hits = search(
        conn,
        workspace_id=workspace_id,
        query=query,
        kinds=["decision"],
        limit=limit,
    )
    return [h.projection for h in hits]


def _prior_failures(
    conn: sqlite3.Connection, workspace_id: str, query: str, limit: int = 3
) -> list[dict[str, Any]]:
    """Search for rejected theories or correction episodes touching this payload."""
    if not query:
        return []
    failures: list[dict[str, Any]] = []
    # Rejected theories.
    rows = conn.execute(
        """SELECT id, title, claim, status, gist FROM theories
           WHERE workspace_id = ? AND status IN ('rejected', 'weakened')
           AND (LOWER(IFNULL(title, '')) LIKE ? OR LOWER(IFNULL(claim, '')) LIKE ?)
           LIMIT ?""",
        (workspace_id, f"%{query.lower()}%", f"%{query.lower()}%", limit),
    ).fetchall()
    for row in rows:
        failures.append(
            {
                "kind": "rejected_theory",
                "id": row[0],
                "title": row[1],
                "status": row[3],
                "gist": row[4] or row[2][:120],
            }
        )
    return failures


def _verdict_from_decision(allow: bool, has_violations: bool) -> str:
    if not allow:
        return "block"
    return "allow_with_advisories" if has_violations else "allow"


def _build_watch_outs(
    applicable_rules: list[dict[str, Any]],
    prior_failures: list[dict[str, Any]],
) -> str:
    """One-line distilled hint or empty string."""
    parts: list[str] = []
    if prior_failures:
        f = prior_failures[0]
        parts.append(f"prior {f['kind']} {f['id']}: {f.get('gist', '')[:80]}")
    if applicable_rules:
        names = ", ".join(r["rule_name"] for r in applicable_rules[:2])
        parts.append(f"active rules: {names}")
    return " | ".join(parts)


# ============================================================
# Main entry point
# ============================================================


def lint(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    tool_name: str,
    tool_payload: dict[str, Any],
    transcript_path: str | None = None,
    ollama_base_url: str | None = None,
    ollama_model: str | None = None,
) -> LintResult:
    """Pre-task lint. Returns LintResult with verdict + advisories.

    Failure-soft: any exception is captured into LintResult.diagnostic
    and the verdict degrades to "allow".
    """
    try:
        rules = load_enforcement_rules(conn, workspace_id)
        trail = read_prior_tool_calls(transcript_path) if transcript_path else []
        decision = decide_with_rules(
            rules,
            tool_name=tool_name,
            tool_input=tool_payload,
            trail=trail,
            ollama_base_url=ollama_base_url,
            ollama_model=ollama_model,
        )
        # Map enforcement violations → applicable_rules advisories.
        applicable: list[dict[str, Any]] = []
        for violation in decision.violations:
            matching_rule = next((r for r in rules if r.id == violation.rule_id), None)
            if matching_rule:
                applicable.append(_rule_to_advisory(matching_rule))
            else:
                applicable.append(
                    {
                        "rule_id": violation.rule_id,
                        "rule_name": violation.rule_name,
                        "level": violation.enforcement_level,
                        "advisory": violation.why,
                    }
                )
        query = _payload_query(tool_payload)
        related = _related_decisions(conn, workspace_id, query)
        failures = _prior_failures(conn, workspace_id, query)
        verdict = _verdict_from_decision(decision.allow, bool(applicable))
        watch_outs = _build_watch_outs(applicable, failures)
        diagnostic = decision.diagnostic if not decision.allow else ""
        return LintResult(
            verdict=verdict,
            applicable_rules=applicable,
            related_decisions=related,
            prior_failures=failures,
            watch_outs=watch_outs,
            diagnostic=diagnostic,
        )
    except (sqlite3.Error, KeyError, ValueError, TypeError) as exc:
        return LintResult(
            verdict="allow",
            diagnostic=f"lint subsystem error: {exc.__class__.__name__}: {exc}",
        )


__all__ = ["LintResult", "lint"]
