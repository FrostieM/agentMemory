"""Process-local telemetry for the human memory UI.

The UI observatory needs live request flow without turning trace events into
durable memory. This module keeps a bounded in-memory ring buffer and exposes
small helpers that routes can call without depending on the browser UI.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from agent_memory_lite.redaction import redact
from agent_memory_lite.utils.time import iso_now

MAX_EVENTS = 500
DEFAULT_REPLAY_LIMIT = 80
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
            "counts": _json_safe_counts(self.counts),
            "snippet": self.snippet,
            "created_at": self.created_at,
        }


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _json_safe_counts(counts: Mapping[str, Any]) -> dict[str, Any]:
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
                str(nested_key): nested_value
                if isinstance(nested_value, str | int | float | bool)
                else str(nested_value)
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
    return text if len(text) <= limit else text[: limit - 1] + "..."


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
            event_id=_new_id("evt"),
            request_id=request_id or _new_id("req"),
            workspace_id=workspace_id,
            type=event_type,
            endpoint=endpoint,
            operation=operation,
            stage=stage,
            label=label,
            status=status,
            duration_ms=duration_ms,
            counts=_json_safe_counts(counts or {}),
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


ui_telemetry = UiTelemetryBus()


class MemoryOperationTrace:
    def __init__(
        self,
        *,
        workspace_id: str,
        endpoint: str,
        operation: str,
        label: str,
        snippet: object = "",
    ) -> None:
        self.workspace_id = workspace_id
        self.endpoint = endpoint
        self.operation = operation
        self.label = label
        self.request_id = _new_id("req")
        self._started_at = time.perf_counter()
        self._stage_started_at: dict[str, float] = {}
        self._snippet = snippet

    def __enter__(self) -> MemoryOperationTrace:
        ui_telemetry.record(
            workspace_id=self.workspace_id,
            request_id=self.request_id,
            event_type="request_started",
            endpoint=self.endpoint,
            operation=self.operation,
            stage="input",
            label=self.label,
            status="running",
            snippet=self._snippet,
        )
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> Literal[False]:
        duration_ms = int((time.perf_counter() - self._started_at) * 1000)
        if exc is None:
            ui_telemetry.record(
                workspace_id=self.workspace_id,
                request_id=self.request_id,
                event_type="request_done",
                endpoint=self.endpoint,
                operation=self.operation,
                stage="response",
                label=f"{self.label} completed",
                status="ok",
                duration_ms=duration_ms,
            )
        else:
            ui_telemetry.record(
                workspace_id=self.workspace_id,
                request_id=self.request_id,
                event_type="request_failed",
                endpoint=self.endpoint,
                operation=self.operation,
                stage="response",
                label=f"{self.label} failed",
                status="error",
                duration_ms=duration_ms,
                snippet=safe_snippet(str(exc)),
            )
        return False

    def stage_started(
        self,
        stage: str,
        label: str,
        *,
        counts: Mapping[str, Any] | None = None,
        snippet: object = "",
    ) -> None:
        self._stage_started_at[stage] = time.perf_counter()
        ui_telemetry.record(
            workspace_id=self.workspace_id,
            request_id=self.request_id,
            event_type="stage_started",
            endpoint=self.endpoint,
            operation=self.operation,
            stage=stage,
            label=label,
            status="running",
            counts=counts,
            snippet=snippet,
        )

    def stage_done(
        self,
        stage: str,
        label: str,
        *,
        status: str = "ok",
        counts: Mapping[str, Any] | None = None,
        snippet: object = "",
    ) -> None:
        started_at = self._stage_started_at.pop(stage, None)
        duration_ms = int((time.perf_counter() - started_at) * 1000) if started_at else None
        ui_telemetry.record(
            workspace_id=self.workspace_id,
            request_id=self.request_id,
            event_type="stage_done",
            endpoint=self.endpoint,
            operation=self.operation,
            stage=stage,
            label=label,
            status=status,
            duration_ms=duration_ms,
            counts=counts,
            snippet=snippet,
        )

    def graph_delta(
        self,
        *,
        object_type: str,
        object_id: str,
        action: str,
        label: str,
        status: str = "ok",
        counts: Mapping[str, Any] | None = None,
    ) -> None:
        ui_telemetry.record(
            workspace_id=self.workspace_id,
            request_id=self.request_id,
            event_type="graph_delta",
            endpoint=self.endpoint,
            operation=self.operation,
            stage="persist",
            label=label,
            status=status,
            counts={
                "object_type": object_type,
                "object_id": object_id,
                "action": action,
                **dict(counts or {}),
            },
        )


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
