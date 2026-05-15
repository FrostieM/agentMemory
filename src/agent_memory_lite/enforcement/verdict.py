"""Verdict + decision types for PreToolUse enforcement.

A tool call payload runs through mechanical and (optionally) semantic
rule checks. Each rule emits zero or more ``RuleViolation``. The hook
collapses them into one ``HookDecision`` and exits 0/2 to Claude Code.

The diagnostic text rendered for the blocked case is intentionally
shaped for the model: lists each ``rule_id`` so the agent can quote
the behavior_instruction id back when it retries.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RuleViolation:
    """One specific rule the tool call would violate.

    ``enforcement_level`` is the tag the rule was loaded with
    (``mechanical`` or ``semantic``). The diagnostic surfaces it so the
    agent knows which layer caught the violation.
    """

    rule_id: str
    rule_name: str
    why: str
    enforcement_level: str


@dataclass(frozen=True, slots=True)
class HookDecision:
    """Final allow/block decision for a single PreToolUse invocation."""

    allow: bool
    violations: list[RuleViolation] = field(default_factory=list)

    @property
    def diagnostic(self) -> str:
        """Human-readable rendering shown to the model on block."""
        if self.allow or not self.violations:
            return ""
        lines = ["[pre-tool-use] tool call blocked — violates active rules:"]
        for v in self.violations:
            lines.append(f"  - {v.rule_id} [{v.enforcement_level}] {v.rule_name}: {v.why}")
        lines.append("Fix the violation and retry the tool call.")
        return "\n".join(lines)


def allow() -> HookDecision:
    """Convenience: tool call is permitted."""
    return HookDecision(allow=True)


def block(violations: list[RuleViolation]) -> HookDecision:
    """Convenience: tool call is denied with one or more violations."""
    return HookDecision(allow=False, violations=violations)
