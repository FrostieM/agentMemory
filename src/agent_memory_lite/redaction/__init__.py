"""Secret redaction.

Public surface:

    from agent_memory_lite.redaction import redact, RedactedText, RedactionSpan
"""

from agent_memory_lite.redaction.redactor import (
    REDACTION_MARKER_PREFIX,
    RedactedText,
    RedactionSpan,
    redact,
)

__all__ = [
    "REDACTION_MARKER_PREFIX",
    "RedactedText",
    "RedactionSpan",
    "redact",
]
