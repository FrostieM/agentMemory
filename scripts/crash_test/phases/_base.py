"""Phase base + shared state object."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass
class CrashTestState:
    """Mutable state passed between phases."""

    workspace_id: str
    workspace_dir: Path
    db_path: Path
    vector_path: Path
    base_url: str
    client: httpx.Client
    conn: sqlite3.Connection  # direct DB access for SQL-level invariants
    settings_env: dict[str, str]
    artifacts_dir: Path
    skip_llm: bool = False
    skip_ui: bool = False
    bag: dict[str, Any] = field(default_factory=dict)  # phase-shared scratch


@dataclass
class PhaseResult:
    name: str
    description: str = ""
    status: str = "pass"  # pass / fail / skip / error
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def assert_eq(self, name: str, got: Any, want: Any) -> bool:
        if got == want:
            self.passed += 1
            return True
        self.failed += 1
        self.status = "fail"
        self.problems.append(f"{name}: got {got!r}, want {want!r}")
        return False

    def assert_true(self, name: str, cond: bool, hint: str = "") -> bool:
        if cond:
            self.passed += 1
            return True
        self.failed += 1
        self.status = "fail"
        msg = f"{name}: condition false"
        if hint:
            msg += f" ({hint})"
        self.problems.append(msg)
        return False

    def assert_in(self, name: str, member: Any, container: Any) -> bool:
        if member in container:
            self.passed += 1
            return True
        self.failed += 1
        self.status = "fail"
        self.problems.append(f"{name}: {member!r} not in {container!r}")
        return False

    def assert_ge(self, name: str, got: Any, want: Any) -> bool:
        if got >= want:
            self.passed += 1
            return True
        self.failed += 1
        self.status = "fail"
        self.problems.append(f"{name}: got {got!r}, want >= {want!r}")
        return False

    def skip(self, reason: str) -> None:
        self.status = "skip"
        self.skipped += 1
        self.notes.append(f"skipped: {reason}")

    def note(self, msg: str) -> None:
        self.notes.append(msg)


class Phase:
    """Subclass and override ``run``. Set ``name`` and ``description``."""

    name: str = "unnamed"
    description: str = ""
    fatal_on_failure: bool = False  # setup phase sets this True

    def run(self, state: CrashTestState) -> PhaseResult:  # pragma: no cover - override
        raise NotImplementedError
