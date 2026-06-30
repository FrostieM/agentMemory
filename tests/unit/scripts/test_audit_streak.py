"""Batch K: certification audit-streak gate tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import audit_streak


def _round(head: str, findings: int) -> dict[str, object]:
    return {"head": head, "findings": findings, "scope": "whole-system"}


def test_fewer_than_required_fails() -> None:
    ok, _ = audit_streak.evaluate([_round("aaa", 0), _round("aaa", 0)], require=3, head=None)
    assert ok is False


def test_clean_streak_passes() -> None:
    rounds = [_round("aaa", 0)] * 3
    ok, msg = audit_streak.evaluate(rounds, require=3, head=None)
    assert ok is True
    assert "certified" in msg


def test_a_dirty_round_breaks_the_streak() -> None:
    rounds = [_round("aaa", 0), _round("aaa", 2), _round("aaa", 0)]
    ok, _ = audit_streak.evaluate(rounds, require=3, head=None)
    assert ok is False


def test_streak_must_be_one_consistent_head() -> None:
    rounds = [_round("aaa", 0), _round("bbb", 0), _round("ccc", 0)]
    ok, msg = audit_streak.evaluate(rounds, require=3, head=None)
    assert ok is False
    assert "one consistent head" in msg


def test_strict_head_mismatch_fails() -> None:
    rounds = [_round("aaa", 0)] * 3
    ok, msg = audit_streak.evaluate(rounds, require=3, head="zzz")  # current HEAD != streak
    assert ok is False
    assert "source changed" in msg


def test_only_the_last_n_rounds_count() -> None:
    # An old dirty round before 3 fresh clean ones at the same head -> still certified.
    rounds = [_round("old", 5), _round("aaa", 0), _round("aaa", 0), _round("aaa", 0)]
    ok, _ = audit_streak.evaluate(rounds, require=3, head=None)
    assert ok is True


def test_record_then_evaluate_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "certification_ledger.jsonl"
    monkeypatch.setattr(audit_streak, "LEDGER", ledger)
    for _ in range(3):
        assert audit_streak.main(["--record", "--head", "deadbeef", "--findings", "0"]) == 0
    assert audit_streak.main(["--require", "3"]) == 0  # 3 clean @ deadbeef
    written = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(written) == 3
    assert all(r["findings"] == 0 and r["head"] == "deadbeef" for r in written)


def test_empty_ledger_is_not_certified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_streak, "LEDGER", tmp_path / "empty.jsonl")
    assert audit_streak.main(["--require", "3"]) == 1
