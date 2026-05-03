"""Pydantic annotation that strips MCP function-call markup from text fields.

Use ``SafeText`` instead of ``str`` for any field that an agent populates
through an MCP tool call where the raw value could plausibly carry the
agent's own function-call markup (`</decision_text>`,
`<parameter name="...">`, `</invoke>`, etc.) leaked across the parameter
boundary. The validator silently truncates at the first marker — clean
input passes through byte-identical (idempotent).

For nullable fields use ``SafeTextOptional``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator

from agent_memory_lite.redaction.mcp_markup import strip_mcp_markup


def _strip_or_passthrough(value: str) -> str:
    out = strip_mcp_markup(value)
    return out if out is not None else value


def _strip_optional(value: str | None) -> str | None:
    return strip_mcp_markup(value)


SafeText = Annotated[str, AfterValidator(_strip_or_passthrough)]
SafeTextOptional = Annotated[str | None, AfterValidator(_strip_optional)]
