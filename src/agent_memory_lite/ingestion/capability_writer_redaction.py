"""Free-text redaction helpers shared by the capability writers."""

from __future__ import annotations

from agent_memory_lite.redaction.redactor import redact


# v3.5 sector-3 audit-followup: every text field on a capability gets
# the same redaction treatment as episode/decision/theory writers got
# earlier. Capabilities ride every brief / envelope via role_activation,
# so an operator pasting `purpose="Use Bearer eyJ... to deploy"` would
# have leaked the token into every future agent context.
def _redact(text: str | None) -> str | None:
    if text is None:
        return None
    return redact(text).text


def _redact_list(items: list[str] | None) -> list[str] | None:
    if not items:
        return items
    return [redact(item).text for item in items]
