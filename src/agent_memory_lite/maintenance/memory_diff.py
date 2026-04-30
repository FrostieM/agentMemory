"""Diff two memory audit/watchdog/dashboard payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_STATUS_RANK = {"ok": 0, "unknown": 0, "warning": 1, "degraded": 2}


@dataclass(frozen=True, slots=True)
class MemoryDiffReport:
    status: str
    before_status: str
    after_status: str
    status_changed: bool
    count_deltas: dict[str, float]
    component_changes: dict[str, dict[str, str]]
    failures_added: list[str] = field(default_factory=list)
    failures_resolved: list[str] = field(default_factory=list)
    warnings_added: list[str] = field(default_factory=list)
    warnings_resolved: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "before_status": self.before_status,
            "after_status": self.after_status,
            "status_changed": self.status_changed,
            "count_deltas": self.count_deltas,
            "component_changes": self.component_changes,
            "failures_added": self.failures_added,
            "failures_resolved": self.failures_resolved,
            "warnings_added": self.warnings_added,
            "warnings_resolved": self.warnings_resolved,
        }


def load_memory_payload(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return parsed


def _status(payload: dict[str, Any]) -> str:
    status = str(payload.get("status", "unknown"))
    return status if status in _STATUS_RANK else "unknown"


def _counts(payload: dict[str, Any]) -> dict[str, float]:
    counts: dict[str, float] = {}

    def add(prefix: str, raw: object) -> None:
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                counts[f"{prefix}{key}"] = float(value)

    add("", payload.get("counts"))
    add("integrity.", _dict_path(payload, "integrity", "counts"))
    add("retrieval_eval.", _dict_path(payload, "retrieval_eval"))
    add("hygiene.", _dict_path(payload, "hygiene", "counts"))
    components = payload.get("components")
    if isinstance(components, dict):
        for name, component in components.items():
            add(f"{name}.", _dict_path(component, "counts"))
            add(f"{name}.integrity.", _dict_path(component, "integrity", "counts"))
            add(f"{name}.hygiene.", _dict_path(component, "hygiene", "counts"))
    return counts


def _dict_path(payload: object, *path: str) -> object:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _component_statuses(payload: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {"root": _status(payload)}
    checks = payload.get("checks")
    if isinstance(checks, dict):
        for name, check in checks.items():
            if isinstance(check, dict):
                statuses[f"check.{name}"] = str(check.get("status", "unknown"))
    for name in ("integrity", "retrieval_eval", "hygiene"):
        component = payload.get(name)
        if isinstance(component, dict):
            statuses[name] = str(component.get("status", "unknown"))
    components = payload.get("components")
    if isinstance(components, dict):
        for name, component in components.items():
            if isinstance(component, dict):
                statuses[f"component.{name}"] = str(component.get("status", "unknown"))
    return statuses


def _messages(payload: dict[str, Any], key: str) -> set[str]:
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            raw = value.get(key)
            if isinstance(raw, list):
                found.update(str(item) for item in raw)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found


def diff_memory_payloads(before: dict[str, Any], after: dict[str, Any]) -> MemoryDiffReport:
    before_status = _status(before)
    after_status = _status(after)
    before_counts = _counts(before)
    after_counts = _counts(after)
    count_deltas = {
        key: after_counts.get(key, 0.0) - before_counts.get(key, 0.0)
        for key in sorted(set(before_counts) | set(after_counts))
        if after_counts.get(key, 0.0) != before_counts.get(key, 0.0)
    }

    before_components = _component_statuses(before)
    after_components = _component_statuses(after)
    component_changes = {
        key: {
            "before": before_components.get(key, "missing"),
            "after": after_components.get(key, "missing"),
        }
        for key in sorted(set(before_components) | set(after_components))
        if before_components.get(key) != after_components.get(key)
    }

    before_failures = _messages(before, "failures")
    after_failures = _messages(after, "failures")
    before_warnings = _messages(before, "warnings")
    after_warnings = _messages(after, "warnings")

    failures_added = sorted(after_failures - before_failures)
    warnings_added = sorted(after_warnings - before_warnings)
    status_rank_delta = _STATUS_RANK.get(after_status, 0) - _STATUS_RANK.get(before_status, 0)
    status = "ok"
    if status_rank_delta > 0 or failures_added or after_status == "degraded":
        status = "degraded"
    elif warnings_added or after_status == "warning":
        status = "warning"

    return MemoryDiffReport(
        status=status,
        before_status=before_status,
        after_status=after_status,
        status_changed=before_status != after_status,
        count_deltas=count_deltas,
        component_changes=component_changes,
        failures_added=failures_added,
        failures_resolved=sorted(before_failures - after_failures),
        warnings_added=warnings_added,
        warnings_resolved=sorted(before_warnings - after_warnings),
    )
