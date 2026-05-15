"""Semantic enforcement layer: Ollama-judged rule violations.

For each ``EnforcementRule`` at level ``semantic`` we build a strict
policy-reviewer prompt (see ``semantic_prompt``) and POST it to a
local Ollama ``/api/chat`` endpoint. The reply is parsed by
``semantic_parse.parse_verdict``; only ``violates=True`` produces a
``RuleViolation``.

Two cost controls keep this affordable when every pinned rule is
auto-routed to the semantic layer:

* :func:`filter_relevant` (see ``semantic_relevance``) prunes rules
  whose ``applies_to`` clearly does not match the current tool call.
* Remaining rules are judged **in parallel** via ``asyncio.gather``;
  one network round-trip in the wall-clock budget instead of N.

Fail-OPEN by design: any transport / parse / timeout error returns
``None`` for that rule. Blocking the agent because Ollama is down is
worse than letting the call through.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from agent_memory_lite.enforcement.rule_loader import EnforcementRule
from agent_memory_lite.enforcement.semantic_parse import parse_verdict
from agent_memory_lite.enforcement.semantic_prompt import build_review_prompt
from agent_memory_lite.enforcement.semantic_relevance import filter_relevant
from agent_memory_lite.enforcement.verdict import RuleViolation

_DEFAULT_TIMEOUT = 8.0


async def _call_ollama_async(prompt: str, *, base_url: str, model: str, timeout: float) -> str:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
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


async def _check_rule_async(
    rule: EnforcementRule,
    *,
    tool_name: str,
    tool_input: dict[str, object],
    trail: list[str],
    base_url: str,
    model: str,
    timeout: float,
) -> RuleViolation | None:
    prompt = build_review_prompt(
        rule_text=rule.rule, tool_name=tool_name, tool_input=tool_input, trail=trail
    )
    content = await _call_ollama_async(prompt, base_url=base_url, model=model, timeout=timeout)
    violates, why = parse_verdict(content)
    if not violates:
        return None
    return RuleViolation(
        rule_id=rule.id,
        rule_name=rule.name,
        why=why or "model flagged a violation without explanation",
        enforcement_level="semantic",
    )


async def _check_semantic_async(
    rules: list[EnforcementRule],
    *,
    tool_name: str,
    tool_input: dict[str, object],
    trail: list[str],
    base_url: str,
    model: str,
    timeout: float,
) -> list[RuleViolation]:
    tasks = [
        _check_rule_async(
            rule,
            tool_name=tool_name,
            tool_input=tool_input,
            trail=trail,
            base_url=base_url,
            model=model,
            timeout=timeout,
        )
        for rule in rules
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, RuleViolation)]


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
    """Run each semantic-tagged rule through Ollama and collect violations.

    Sync entry point — spins up a fresh event loop for parallel
    judging. Safe inside the hook subprocess because no event loop
    is already running there.
    """
    semantic_rules = [r for r in rules if r.level == "semantic"]
    relevant = filter_relevant(semantic_rules, tool_name=tool_name)
    if not relevant:
        return []
    return asyncio.run(
        _check_semantic_async(
            relevant,
            tool_name=tool_name,
            tool_input=tool_input,
            trail=trail,
            base_url=base_url,
            model=model,
            timeout=timeout,
        )
    )
