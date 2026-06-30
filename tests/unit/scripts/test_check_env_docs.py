"""Batch I: settings env-var documentation gate tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_env_docs


def test_committed_env_example_documents_every_var() -> None:
    """The real .env.example (post-backfill) must document every settings env var."""
    assert check_env_docs.main(["--enforce"]) == 0


def test_missing_var_fails_enforce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env.example"
    env.write_text("MEMORY_API_PORT=8765\n", encoding="utf-8")  # only one var
    monkeypatch.setattr(check_env_docs, "ENV_EXAMPLE", env)
    assert check_env_docs.main(["--enforce"]) == 1


def test_write_backfills_then_enforce_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env.example"
    env.write_text("", encoding="utf-8")
    monkeypatch.setattr(check_env_docs, "ENV_EXAMPLE", env)
    assert check_env_docs.main(["--write"]) == 0
    assert check_env_docs.main(["--enforce"]) == 0  # complete after backfill
    # The backfilled file actually contains a known async-write knob.
    assert "MEMORY_DEFER_EXTRACTION=" in env.read_text(encoding="utf-8")
