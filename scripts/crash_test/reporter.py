"""Collect PhaseResults + render text/JSON summary."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.crash_test.phases._base import PhaseResult


class Reporter:
    def __init__(self) -> None:
        self.results: list[PhaseResult] = []

    def record(self, result: PhaseResult) -> None:
        self.results.append(result)

    def overall_status(self) -> str:
        if any(r.status == "error" for r in self.results):
            return "error"
        if any(r.status == "fail" for r in self.results):
            return "fail"
        if all(r.status == "skip" for r in self.results):
            return "skip"
        return "pass"

    def totals(self) -> dict[str, int]:
        return {
            "phases": len(self.results),
            "phases_pass": sum(1 for r in self.results if r.status == "pass"),
            "phases_fail": sum(1 for r in self.results if r.status == "fail"),
            "phases_skip": sum(1 for r in self.results if r.status == "skip"),
            "phases_error": sum(1 for r in self.results if r.status == "error"),
            "assertions_pass": sum(r.passed for r in self.results),
            "assertions_fail": sum(r.failed for r in self.results),
        }

    def all_problems(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for r in self.results:
            for problem in r.problems:
                out.append({"phase": r.name, "problem": problem})
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status(),
            "totals": self.totals(),
            "phases": [asdict(r) for r in self.results],
            "problems": self.all_problems(),
        }

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def render_text(self) -> str:
        lines: list[str] = []
        for r in self.results:
            lines.append(self._render_phase(r))
        totals = self.totals()
        lines.append("─" * 60)
        lines.append(
            f"Total: {totals['phases']} phases "
            f"({totals['phases_pass']} pass, {totals['phases_fail']} fail, "
            f"{totals['phases_skip']} skip), "
            f"{totals['assertions_pass']}/{totals['assertions_pass'] + totals['assertions_fail']} assertions"
        )
        lines.append(f"Overall: {self.overall_status().upper()}")
        problems = self.all_problems()
        if problems:
            lines.append("")
            lines.append(f"Problems ({len(problems)}):")
            for p in problems:
                lines.append(f"  [{p['phase']}] {p['problem']}")
        return "\n".join(lines)

    @staticmethod
    def _render_phase(r: PhaseResult) -> str:
        tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "error": "ERR "}.get(
            r.status, r.status.upper()
        )
        head = (
            f"[{tag}] {r.name:30} "
            f"{r.passed:3}/{r.passed + r.failed:<3} assertions  "
            f"{r.duration_seconds:5.2f}s"
        )
        if r.notes:
            head += "  | " + "; ".join(r.notes[:2])
        return head
