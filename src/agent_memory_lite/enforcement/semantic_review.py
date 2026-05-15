"""Semantic enforcement layer: Ollama-judged rule violations.

For each ``EnforcementRule`` at level ``semantic`` we build a strict
policy-reviewer prompt (see ``semantic_prompt``) and POST it to a
local Ollama ``/api/chat`` endpoint. The model replies with a JSON
object ``{"violates": bool, "why": str}``; we parse it and emit a
``RuleViolation`` only when ``violates=true``.

Fail-OPEN by design: any transport / parse / timeout error returns
``None`` for that rule. Blocking the agent because Ollama is down is
worse than letting the call through — the foreground reminder still
runs, and mechanical rules already short-circuit by the time we get
here.

The check is run sequentially across rules. Ollama is fast enough
locally (~1-3s per call) that 2-3 semantic rules per tool invocation
stay under perceived latency. Parallel asyncio could be added later
if the semantic rule set grows large.
"""

from __future__ import annotations

import json
import re

import httpx

from agent_memory_lite.enforcement.rule_loader import EnforcementRule
from agent_memory_lite.enforcement.semantic_prompt import build_review_prompt
from agent_memory_lite.enforcement.verdict import RuleViolation

_DEFAULT_TIMEOUT = 8.0
_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _strip_fences(content: str) -> str:
    match = _FENCE_RE.search(content)
    if match:
        return match.group(1).strip()
    start = content.find("{")
    end = content.rfind("}")
    if 0 <= start < end:
        return content[start : end + 1]
    return content.strip()


def _parse_verdict(content: str) -> tuple[bool, str]:
    """Return ``(violates, why)`` from the model response; defaults to (False, '')."""
    if not content.strip():
        return False, ""
    cleaned = _strip_fences(content)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return False, ""
    if not isinstance(payload, dict):
        return False, ""
    violates = bool(payload.get("violates"))
    why = str(payload.get("why") or "").strip()
    return violates, why


def _call_ollama(prompt: str, *, base_url: str, model: str, timeout: float) -> str:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                f"{base_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0.0},
                },
            )
            response.raise_for_status()
    except httpx.HTTPError:
        return ""
    try:
        body = response.json()
    except json.JSONDecodeError:
        return ""
    message = body.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def check_semantic(
    rules: list[EnforcementRule],
    *,
    tool_name: str,
    tool_input: dict[str, object],
    trail: list[str],
    base_url: str,
    model: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[RuleViolation]:
    """Run each semantic-tagged rule through Ollama and collect violations."""
    violations: list[RuleViolation] = []
    for rule in rules:
        if rule.level != "semantic":
            continue
        prompt = build_review_prompt(
            rule_text=rule.rule,
            tool_name=tool_name,
            tool_input=tool_input,
            trail=trail,
        )
        content = _call_ollama(prompt, base_url=base_url, model=model, timeout=timeout)
        violates, why = _parse_verdict(content)
        if not violates:
            continue
        violations.append(
            RuleViolation(
                rule_id=rule.id,
                rule_name=rule.name,
                why=why or "model flagged a violation without explanation",
                enforcement_level="semantic",
            )
        )
    return violations
