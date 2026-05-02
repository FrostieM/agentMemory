"""Summarize memory audit/watchdog history artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TrendRun:
    path: str
    generated_at: str
    status: str
    integrity_status: str
    retrieval_eval_status: str
    hygiene_status: str
    failures_count: int
    warnings_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "generated_at": self.generated_at,
            "status": self.status,
            "integrity_status": self.integrity_status,
            "retrieval_eval_status": self.retrieval_eval_status,
            "hygiene_status": self.hygiene_status,
            "failures_count": self.failures_count,
            "warnings_count": self.warnings_count,
        }


@dataclass(frozen=True, slots=True)
class TrendReport:
    status: str
    audit_dir: str
    runs: list[TrendRun] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "audit_dir": self.audit_dir,
            "runs": [run.to_dict() for run in self.runs],
            "counts": dict(self.counts),
            "warnings": list(self.warnings),
        }


def _load_run(path: Path) -> TrendRun | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    integrity = payload.get("integrity", {})
    retrieval = payload.get("retrieval_eval", {})
    hygiene = payload.get("hygiene", {})
    return TrendRun(
        path=str(path),
        generated_at=str(payload.get("generated_at", "")),
        status=str(payload.get("status", "unknown")),
        integrity_status=str(integrity.get("status", "unknown"))
        if isinstance(integrity, dict)
        else "unknown",
        retrieval_eval_status=str(retrieval.get("status", "unknown"))
        if isinstance(retrieval, dict)
        else "unknown",
        hygiene_status=str(hygiene.get("status", "unknown"))
        if isinstance(hygiene, dict)
        else "unknown",
        failures_count=len(payload.get("failures", []))
        if isinstance(payload.get("failures", []), list)
        else 0,
        warnings_count=len(payload.get("warnings", []))
        if isinstance(payload.get("warnings", []), list)
        else 0,
    )


def build_trend_report(audit_dir: Path, *, limit: int = 20) -> TrendReport:
    runs = [
        run
        for path in sorted(audit_dir.glob("*.json"), key=lambda item: item.stat().st_mtime)
        if (run := _load_run(path)) is not None
    ][-limit:]
    counts: dict[str, int] = {
        "runs": len(runs),
        "ok": sum(1 for run in runs if run.status == "ok"),
        "warning": sum(1 for run in runs if run.status == "warning"),
        "degraded": sum(1 for run in runs if run.status == "degraded"),
        "unknown": sum(1 for run in runs if run.status == "unknown"),
    }
    warnings: list[str] = []
    if not runs:
        warnings.append("no audit/watchdog artifacts found")
    if runs and runs[-1].status != "ok":
        warnings.append(f"latest audit status is {runs[-1].status}")
    # Historical degradation remains visible in counts/runs but does not make a
    # currently healthy DB warning-level. The latest run is the trust signal;
    # history is drift evidence for humans.
    status = "ok" if runs and not warnings else "warning"
    if runs and runs[-1].status == "degraded":
        status = "degraded"
    return TrendReport(
        status=status,
        audit_dir=str(audit_dir),
        runs=runs,
        counts=counts,
        warnings=warnings,
    )
