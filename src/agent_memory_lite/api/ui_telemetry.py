"""Process-local telemetry for the human memory UI.

The UI observatory needs live request flow without turning trace events into
durable memory. The wire-shape event + helpers live in
``ui_telemetry_event.py``; the ring-buffer bus lives in
``ui_telemetry_bus.py``; the per-operation context manager lives in
``ui_telemetry_trace.py``. This module owns the SSE event stream a
route can yield to the browser plus the public re-export surface.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from agent_memory_lite.api.ui_telemetry_bus import (
    DEFAULT_REPLAY_LIMIT,
    MAX_EVENTS,
    UiTelemetryBus,
    ui_telemetry,
)
from agent_memory_lite.api.ui_telemetry_event import (
    SNIPPET_LIMIT,
    UiTelemetryEvent,
    safe_snippet,
)
from agent_memory_lite.api.ui_telemetry_trace import MemoryOperationTrace

__all__ = [
    "DEFAULT_REPLAY_LIMIT",
    "MAX_EVENTS",
    "SNIPPET_LIMIT",
    "MemoryOperationTrace",
    "UiTelemetryBus",
    "UiTelemetryEvent",
    "event_stream",
    "safe_snippet",
    "sse_payload",
    "trace_memory_operation",
    "ui_telemetry",
]


def trace_memory_operation(
    *,
    workspace_id: str,
    endpoint: str,
    operation: str,
    label: str,
    snippet: object = "",
) -> MemoryOperationTrace:
    return MemoryOperationTrace(
        workspace_id=workspace_id,
        endpoint=endpoint,
        operation=operation,
        label=label,
        snippet=snippet,
    )


def sse_payload(event: dict[str, Any]) -> str:
    return (
        f"id: {event['event_id']}\nevent: memory\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


def event_stream(
    *,
    workspace_id: str,
    since: str | None,
    once: bool = False,
    heartbeat_seconds: float = 15.0,
) -> Iterator[str]:
    last_event_id = since
    replay = ui_telemetry.snapshot(workspace_id=workspace_id, since=since)
    for event in replay:
        last_event_id = str(event["event_id"])
        yield sse_payload(event)
    if once:
        return

    while True:
        events = ui_telemetry.wait_for_events(
            workspace_id=workspace_id,
            after_event_id=last_event_id,
            timeout_seconds=heartbeat_seconds,
        )
        if not events:
            yield ": heartbeat\n\n"
            continue
        for event in events:
            last_event_id = str(event["event_id"])
            yield sse_payload(event)
