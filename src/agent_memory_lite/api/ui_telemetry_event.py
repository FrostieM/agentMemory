"""Wire-shape event + helpers for the UI telemetry surface.

Split out of ``ui_telemetry.py`` so each module stays under the SLOC
ceiling. Holds the ``UiTelemetryEvent`` dataclass plus the small
helpers (id generator, counts sanitizer, snippet redactor) every
caller of the telemetry bus needs.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.redaction import redact
from agent_memory_lite.utils.text_encoding import repair_common_mojibake
from agent_memory_lite.utils.time import iso_now

SNIPPET_LIMIT = 180


@dataclass(frozen=True)
class UiTelemetryEvent:
    event_id: str
    request_id: str
    workspace_id: str
    type: str
    endpoint: str
    operation: str
    stage: str
    label: str
    status: str
    duration_ms: int | None = None
    counts: dict[str, Any] = field(default_factory=dict)
    snippet: str = ""
    created_at: str = field(default_factory=iso_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "request_id": self.request_id,
            "workspace_id": self.workspace_id,
            "type": self.type,
            "endpoint": self.endpoint,
            "operation": self.operation,
            "stage": self.stage,
            "label": self.label,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "counts": json_safe_counts(self.counts),
            "snippet": self.snippet,
            "created_at": self.created_at,
        }


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def json_safe_counts(counts: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in counts.items():
        if value is None or isinstance(value, str | int | float | bool):
            safe[key] = value
        elif isinstance(value, list | tuple):
            safe[key] = [
                item if isinstance(item, str | int | float | bool) else str(item) for item in value
            ]
        elif isinstance(value, dict):
            safe[key] = {
                str(nested_key): (
                    nested_value
                    if isinstance(nested_value, str | int | float | bool)
                    else str(nested_value)
                )
                for nested_key, nested_value in value.items()
            }
        else:
            safe[key] = str(value)
    return safe


def safe_snippet(value: object, *, limit: int = SNIPPET_LIMIT) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        raw = value
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            raw = str(value)
    text = redact(raw, include_pii=False).text.replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    # Repair common mojibake before publishing on the SSE wire.
    # Russian queries typed in a Windows console (cp1251) and forwarded
    # by curl arrive at the server as UTF-8 bytes that round-tripped
    # through cp1252; the SQLite store keeps the corrupted form, but
    # the live trace surface should never display the broken
    # representation. The repair walks UTF-8↔cp1252 and UTF-8↔cp1251
    # round-trips, no-op on already-clean text.
    text = repair_common_mojibake(text)
    return text if len(text) <= limit else text[: limit - 1] + "..."
