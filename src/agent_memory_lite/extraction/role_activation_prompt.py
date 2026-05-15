"""Build a role-activation system-reminder from top-ranked workspace capabilities.

Companion to ``memory_audit_prompt``. Forces the agent to declare which
ROLE/SKILL it operates under — promoting capability application from ambient
envelope context to a public foreground commitment in the response.

``decide_role_activation`` is offline-testable and returns ``inject=False`` on
any malformed input. ``fetch_top_capabilities`` is the HTTP boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

_MIN_PROMPT_LEN = 30
_MIN_CONFIDENCE = 0.7
_MAX_PURPOSE_CHARS = 280


@dataclass(frozen=True, slots=True)
class RoleActivation:
    """Decision: inject the [role-activation] reminder, with the prompt text."""

    inject: bool
    prompt: str


def _trim(text: str, max_len: int = _MAX_PURPOSE_CHARS) -> str:
    """Soft-trim long text at the last space within ``max_len``."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut + "..."


def _first_confident(items: list[Any], *, min_confidence: float) -> dict[str, Any] | None:
    """Return the first active capability entry above the confidence floor."""
    for item in items:
        if not isinstance(item, dict):
            continue
        if not item.get("active", True):
            continue
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        if conf < min_confidence:
            continue
        return item
    return None


def decide_role_activation(
    *,
    user_prompt: str,
    capabilities: dict[str, Any] | None,
    min_prompt_len: int = _MIN_PROMPT_LEN,
    min_confidence: float = _MIN_CONFIDENCE,
) -> RoleActivation:
    """Decide whether to inject a [role-activation] reminder for this turn.

    Returns ``inject=True`` only when:
    * ``user_prompt`` length (after strip) >= ``min_prompt_len`` (filters
      trivial replies like "ok"/"делай"), AND
    * ``capabilities['roles']`` contains at least one active role with
      ``confidence >= min_confidence``.

    Skills/playbooks are best-effort additions to the prompt.
    """
    if not user_prompt or len(user_prompt.strip()) < min_prompt_len:
        return RoleActivation(inject=False, prompt="")
    if not isinstance(capabilities, dict):
        return RoleActivation(inject=False, prompt="")
    roles = capabilities.get("roles") or []
    if not isinstance(roles, list) or not roles:
        return RoleActivation(inject=False, prompt="")
    top_role = _first_confident(list(roles), min_confidence=min_confidence)
    if top_role is None:
        return RoleActivation(inject=False, prompt="")
    skills = capabilities.get("skills") or []
    top_skill = (
        _first_confident(list(skills), min_confidence=min_confidence)
        if isinstance(skills, list)
        else None
    )
    role_name = top_role.get("name") or "(unnamed role)"
    role_purpose = _trim(top_role.get("purpose") or "(no purpose recorded)")
    lines = [
        "[role-activation] Top capability matches for this task:",
        f"  ROLE:    {role_name}",
        f"  PURPOSE: {role_purpose}",
    ]
    if top_skill is not None:
        skill_name = top_skill.get("name") or "(unnamed skill)"
        skill_summary = _trim(top_skill.get("summary") or "(no summary recorded)")
        lines.append(f"  SKILL:   {skill_name}")
        lines.append(f"  METHOD:  {skill_summary}")
    lines.append("")
    lines.append("REQUIRED in your response:")
    lines.append('  1. First sentence: "Acting as <role>." (verbatim opening).')
    lines.append("  2. Apply the ROLE purpose as a hard boundary — if a step")
    lines.append("     violates it, STOP and surface the conflict.")
    if top_skill is not None:
        lines.append("  3. Use the SKILL method, not ad-hoc reasoning.")
    lines.append("")
    lines.append("Skipping role activation is a discipline violation.")
    return RoleActivation(inject=True, prompt="\n".join(lines))


def fetch_top_capabilities(
    *,
    workspace_id: str,
    query: str,
    base_url: str,
    headers: dict[str, str] | None = None,
    limit: int = 3,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """POST /memory/list_agent_capabilities with the user query. Returns ``None`` on error."""
    payload = {"workspace_id": workspace_id, "query": query, "limit": limit}
    try:
        response = httpx.post(
            f"{base_url}/memory/list_agent_capabilities",
            json=payload,
            headers=headers or {},
            timeout=timeout,
        )
    except (httpx.ConnectError, httpx.HTTPError):
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None
