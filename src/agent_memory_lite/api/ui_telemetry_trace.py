"""``MemoryOperationTrace`` context manager.

Split out of ``ui_telemetry.py`` so each module stays under the SLOC
ceiling. Routes use this trace to record a coordinated
request_started → stage_* → graph_delta(s) → request_done sequence on
the shared bus.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal

from agent_memory_lite.api.ui_telemetry_bus import ui_telemetry
from agent_memory_lite.api.ui_telemetry_event import new_id, safe_snippet


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
        self.request_id = new_id("req")
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
        stage: str = "persist",
        status: str = "ok",
        counts: Mapping[str, Any] | None = None,
    ) -> None:
        ui_telemetry.record(
            workspace_id=self.workspace_id,
            request_id=self.request_id,
            event_type="graph_delta",
            endpoint=self.endpoint,
            operation=self.operation,
            stage=stage,
            label=label,
            status=status,
            counts={
                "object_type": object_type,
                "object_id": object_id,
                "action": action,
                **dict(counts or {}),
            },
        )
