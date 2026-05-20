"""Build a [role-activation] system-reminder from ranked workspace capabilities.

Multi-role hedges ranker errors. ``decide_role_activation`` is offline-testable;
``fetch_top_capabilities`` is the HTTP boundary.

v3.2 rewrite: previous version forced the agent to open every reply with
"Acting as role stack: X + Y" verbatim and warned that skipping was a
"discipline violation." That turned roles into stage props — the agent
*performed* having a skill instead of *using* it, and burned 50-100
output tokens per turn on the announcement. Humans don't say "Acting as
Doctor" before each diagnosis; they internalize their training and
apply it. The reminder now informs the agent which capabilities are
relevant — roles + purposes + skill method stay surfaced — but does
NOT prescribe an opening sentence or threaten discipline action.
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


def _top_n_confident(items: list[Any], *, n: int, min_confidence: float) -> list[dict[str, Any]]:
    """Return up to ``n`` active capability entries above the confidence floor."""
    out: list[dict[str, Any]] = []
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
        out.append(item)
        if len(out) >= n:
            break
    return out


_LABELS = ("PRIMARY", "SECONDARY", "TERTIARY")


def _render_role(role: dict[str, Any], *, label: str | None) -> list[str]:
    name = role.get("name") or "(unnamed)"
    purpose = _trim(
        role.get("purpose") or "(no purpose)", max_len=200 if label else _MAX_PURPOSE_CHARS
    )
    if label is None:
        return [f"  ROLE:    {name}", f"  PURPOSE: {purpose}"]
    return [f"  ROLE {label}: {name}", f"    purpose: {purpose}"]


def decide_role_activation(
    *,
    user_prompt: str,
    capabilities: dict[str, Any] | None,
    min_prompt_len: int = _MIN_PROMPT_LEN,
    min_confidence: float = _MIN_CONFIDENCE,
    top_k_roles: int = 2,
) -> RoleActivation:
    """Decide whether to inject a [role-activation] reminder; multi-role stack hedges ranker errors."""
    if not user_prompt or len(user_prompt.strip()) < min_prompt_len:
        return RoleActivation(inject=False, prompt="")
    if not isinstance(capabilities, dict):
        return RoleActivation(inject=False, prompt="")
    roles = capabilities.get("roles") or []
    if not isinstance(roles, list) or not roles:
        return RoleActivation(inject=False, prompt="")
    top_roles = _top_n_confident(list(roles), n=max(1, top_k_roles), min_confidence=min_confidence)
    if not top_roles:
        return RoleActivation(inject=False, prompt="")
    skills_field = capabilities.get("skills") or []
    top_skills = (
        _top_n_confident(list(skills_field), n=1, min_confidence=min_confidence)
        if isinstance(skills_field, list)
        else []
    )
    top_skill = top_skills[0] if top_skills else None
    single = len(top_roles) == 1
    lines = ["[role-activation] Capabilities active for this task:"]
    if single:
        lines.extend(_render_role(top_roles[0], label=None))
    else:
        for idx, role in enumerate(top_roles):
            label = _LABELS[idx] if idx < len(_LABELS) else f"ROLE-{idx + 1}"
            lines.extend(_render_role(role, label=label))
    if top_skill is not None:
        lines.append(f"  SKILL:   {top_skill.get('name') or '(unnamed skill)'}")
        lines.append(f"  METHOD:  {_trim(top_skill.get('summary') or '(no summary recorded)')}")
    lines.append("")
    lines.append(
        "Apply role purposes as soft constraints on your reasoning. "
        "Prefer the SKILL method over ad-hoc reasoning when relevant. "
        "Internalize and act — no need to announce role activation in your reply."
    )
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
    try:
        response = httpx.post(
            f"{base_url}/memory/list_agent_capabilities",
            json={"workspace_id": workspace_id, "query": query, "limit": limit},
            headers=headers or {},
            timeout=timeout,
        )
        if response.status_code != 200:
            return None
        body = response.json()
    except (httpx.ConnectError, httpx.HTTPError, ValueError):
        return None
    return body if isinstance(body, dict) else None
