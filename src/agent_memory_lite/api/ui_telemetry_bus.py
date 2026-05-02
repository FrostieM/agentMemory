"""Process-local ring buffer for UI telemetry events.

Split out of ``ui_telemetry.py`` so the bus class lives in its own
module. The bus is intentionally process-local: events are not durable
and never reach SQLite. The single module-level ``ui_telemetry``
instance is exported so all callers share one buffer.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Mapping
from typing import Any

from agent_memory_lite.api.ui_telemetry_event import (
    UiTelemetryEvent,
    json_safe_counts,
    new_id,
    safe_snippet,
)

MAX_EVENTS = 500
DEFAULT_REPLAY_LIMIT = 80


class UiTelemetryBus:
    def __init__(self, *, max_events: int = MAX_EVENTS) -> None:
        self._events: deque[UiTelemetryEvent] = deque(maxlen=max_events)
        self._active_requests: dict[str, UiTelemetryEvent] = {}
        self._condition = threading.Condition()

    def clear(self) -> None:
        with self._condition:
            self._events.clear()
            self._active_requests.clear()
            self._condition.notify_all()

    def record(
        self,
        *,
        workspace_id: str,
        event_type: str,
        endpoint: str,
        operation: str,
        stage: str,
        label: str,
        status: str,
        request_id: str | None = None,
        duration_ms: int | None = None,
        counts: Mapping[str, Any] | None = None,
        snippet: object = "",
    ) -> UiTelemetryEvent:
        event = UiTelemetryEvent(
            event_id=new_id("evt"),
            request_id=request_id or new_id("req"),
            workspace_id=workspace_id,
            type=event_type,
            endpoint=endpoint,
            operation=operation,
            stage=stage,
            label=label,
            status=status,
            duration_ms=duration_ms,
            counts=json_safe_counts(counts or {}),
            snippet=safe_snippet(snippet),
        )
        with self._condition:
            self._events.append(event)
            if event.type == "request_started":
                self._active_requests[event.request_id] = event
            elif event.type in {"request_done", "request_failed"}:
                self._active_requests.pop(event.request_id, None)
            self._condition.notify_all()
        return event

    def snapshot(
        self,
        *,
        workspace_id: str,
        since: str | None = None,
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> list[dict[str, Any]]:
        with self._condition:
            events = [event for event in self._events if event.workspace_id == workspace_id]
        if since:
            for index, event in enumerate(events):
                if event.event_id == since:
                    return [item.as_dict() for item in events[index + 1 : index + 1 + limit]]
        return [event.as_dict() for event in events[-limit:]]

    def active_requests(self, *, workspace_id: str) -> list[dict[str, Any]]:
        with self._condition:
            events = [
                event.as_dict()
                for event in self._active_requests.values()
                if event.workspace_id == workspace_id
            ]
        return sorted(events, key=lambda event: str(event.get("created_at") or ""), reverse=True)

    def graph_deltas(self, *, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._condition:
            events = [
                event
                for event in self._events
                if event.workspace_id == workspace_id and event.type == "graph_delta"
            ]
        return [event.as_dict() for event in events[-limit:]]

    def wait_for_events(
        self,
        *,
        workspace_id: str,
        after_event_id: str | None,
        timeout_seconds: float,
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> list[dict[str, Any]]:
        events = self.snapshot(workspace_id=workspace_id, since=after_event_id, limit=limit)
        if events:
            return events
        with self._condition:
            self._condition.wait(timeout=timeout_seconds)
        return self.snapshot(workspace_id=workspace_id, since=after_event_id, limit=limit)


# Process-local singleton.
ui_telemetry = UiTelemetryBus()
