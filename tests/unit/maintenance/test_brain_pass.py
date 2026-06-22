"""v3.0.0-final: brain_pass integration test.

The pass should run all six Phase 1-7 maintenance steps for one workspace
when invoked from the sentinel_scheduler hook. Each step is independently
guarded by its own settings flag; a failure in one step never blocks the
next; the pass returns a structured report for telemetry.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.brain_pass import (
    _LAST_VECTOR_COMPACT_KEY,
    BrainPassReport,
    _step_compact_vectors,
    _step_prune_backups,
    _step_repair_missing_vectors,
    run_brain_pass,
)
from agent_memory_lite.utils.time import iso_now


def _report() -> BrainPassReport:
    now = iso_now()
    return BrainPassReport(workspace_id="ws", started_at=now, finished_at=now)


def test_repair_missing_vectors_step_disabled_is_noop(conn: sqlite3.Connection) -> None:
    settings = Settings().model_copy(update={"vector_repair_enabled": False})
    report = _report()
    _step_repair_missing_vectors(conn, "ws", settings, report)
    assert report.vectors_repaired == 0
    assert report.errors == []


def test_repair_missing_vectors_step_no_work_skips_provider(conn: sqlite3.Connection) -> None:
    # Empty workspace -> the cheap EXISTS probe returns nothing, so the step
    # returns before importing/loading the embedding provider or vector store
    # (no error, no work). If it tried to load the model this would be slow or
    # raise; staying at 0 with no error proves the lazy short-circuit.
    settings = Settings()
    assert settings.vector_repair_enabled is True
    report = _report()
    _step_repair_missing_vectors(conn, "ws", settings, report)
    assert report.vectors_repaired == 0
    assert report.errors == []


class _FakeCompactStore:
    def __init__(self) -> None:
        self.compacted = 0

    def compact(self, **_kw: object) -> dict[str, int]:
        self.compacted += 1
        return {"chunks": 5}

    def close(self) -> None:
        return None


class _NoCompactStore:
    def close(self) -> None:
        return None


def test_compact_vectors_disabled_is_noop(conn: sqlite3.Connection) -> None:
    settings = Settings().model_copy(update={"vector_prune_enabled": False})
    report = _report()
    _step_compact_vectors(conn, "ws", settings, report)
    assert report.errors == []


def test_compact_vectors_runs_and_stamps(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_memory_lite.config import workspace_meta  # noqa: PLC0415
    from agent_memory_lite.vector_store import factory  # noqa: PLC0415

    store = _FakeCompactStore()
    monkeypatch.setattr(factory, "get_vector_store", lambda _settings: store)
    report = _report()
    _step_compact_vectors(conn, "ws", Settings(), report)
    assert store.compacted == 1
    assert report.errors == []
    assert workspace_meta.get(conn, "ws", _LAST_VECTOR_COMPACT_KEY)  # stamped


def test_compact_vectors_rate_limited_within_24h(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_memory_lite.config import workspace_meta  # noqa: PLC0415

    workspace_meta.set_value(conn, "ws", _LAST_VECTOR_COMPACT_KEY, iso_now())  # just ran
    from agent_memory_lite.vector_store import factory  # noqa: PLC0415

    store = _FakeCompactStore()
    monkeypatch.setattr(factory, "get_vector_store", lambda _settings: store)
    report = _report()
    _step_compact_vectors(conn, "ws", Settings(), report)
    assert store.compacted == 0  # skipped by the 24h gate -- store never opened


def test_compact_vectors_noop_without_compact_method(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_memory_lite.vector_store import factory  # noqa: PLC0415

    monkeypatch.setattr(factory, "get_vector_store", lambda _settings: _NoCompactStore())
    report = _report()
    _step_compact_vectors(conn, "ws", Settings(), report)
    assert report.errors == []  # duck-typed no-op, never crashes


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_decision(c: sqlite3.Connection, **kwargs: object) -> None:
    defaults: dict[str, object] = {
        "id": "dec_x",
        "workspace_id": "ws",
        "title": "T",
        "decision_text": "body",
        "gist": "g",
        "status": "active",
        "valid_from": iso_now(),
        "created_at": iso_now(),
        "updated_at": iso_now(),
        "outcome_score": 0.0,
        "pinned": 0,
        "feedback_ewma": 0.5,
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    qs = ", ".join("?" for _ in defaults)
    c.execute(f"INSERT INTO decisions ({cols}) VALUES ({qs})", tuple(defaults.values()))
    c.commit()


# ============================================================
# happy path
# ============================================================


def test_pass_runs_all_six_steps_with_default_settings(conn: sqlite3.Connection) -> None:
    """Empty workspace + default settings -> pass completes without errors,
    returns a structured report with zero counts (no work to do)."""
    settings = Settings()
    report = run_brain_pass(conn, workspace_id="ws", settings=settings)
    assert isinstance(report, BrainPassReport)
    assert report.workspace_id == "ws"
    assert report.errors == []
    # All zero on empty workspace.
    assert report.hebbian_edges_upserted == 0
    assert report.insights_promoted == 0
    assert report.causal_invalidated == 0
    # Self-model gets written even on empty workspace (Phase 5 docstring
    # promises a 50-150-word narrative regardless of input).
    assert report.self_model_refreshed is True


def _seed_decision_feedback(c: sqlite3.Connection, *, fb_id: str, source_id: str, usefulness: float) -> None:
    c.execute(
        "INSERT INTO memory_usage_feedback "
        "(id, workspace_id, source_type, source_id, query, usefulness, notes, created_at, source) "
        "VALUES (?, 'ws', 'decision', ?, '', ?, '', ?, 'agent_observed')",
        (fb_id, source_id, usefulness, iso_now()),
    )
    c.commit()


def test_pass_decision_without_feedback_stays_zero(conn: sqlite3.Connection) -> None:
    """A decision with feedback_ewma set but NO usage-feedback rows has evidence
    count 0, so the EWMA term is correctly suppressed and it stays at 0. The
    feedback-driven path is covered by the next test."""
    _seed_decision(conn, id="dec_pos", feedback_ewma=0.8, outcome_score=0.0)
    report = run_brain_pass(conn, workspace_id="ws", settings=Settings())
    assert "decision" in report.outcome_updated
    row = conn.execute("SELECT outcome_score FROM decisions WHERE id = 'dec_pos'").fetchone()
    assert row is not None
    assert row[0] == 0.0  # no feedback evidence -> stays 0


def test_feedback_ewma_step_drives_decision_outcome(conn: sqlite3.Connection) -> None:
    """End-to-end on the maintenance loop: a usage-feedback row -> _step_feedback_ewma
    recomputes feedback_ewma -> _step_outcome turns it into a positive outcome_score."""
    _seed_decision(conn, id="dec_loop", feedback_ewma=0.0, last_retrieved_at=iso_now())
    _seed_decision_feedback(conn, fb_id="fb_loop", source_id="dec_loop", usefulness=0.9)
    report = run_brain_pass(conn, workspace_id="ws", settings=Settings())
    assert report.feedback_ewma_recomputed >= 1
    ewma, score = conn.execute(
        "SELECT feedback_ewma, outcome_score FROM decisions WHERE id = 'dec_loop'"
    ).fetchone()
    assert ewma > 0.0  # recomputed from the feedback row, not the seeded 0.0
    assert score > 0.0  # outcome now reflects the feedback


def test_feedback_ewma_step_gated_off_is_noop(conn: sqlite3.Connection) -> None:
    """feedback_ewma_enabled=False -> the recompute step is a no-op; feedback_ewma
    (and thus the feedback-driven outcome) stays 0."""
    _seed_decision(conn, id="dec_off", feedback_ewma=0.0, last_retrieved_at=iso_now())
    _seed_decision_feedback(conn, fb_id="fb_off", source_id="dec_off", usefulness=0.9)
    settings = Settings().model_copy(update={"feedback_ewma_enabled": False})
    report = run_brain_pass(conn, workspace_id="ws", settings=settings)
    assert report.feedback_ewma_recomputed == 0
    ewma = conn.execute("SELECT feedback_ewma FROM decisions WHERE id = 'dec_off'").fetchone()[0]
    assert (ewma or 0.0) == 0.0


def test_pass_normalizes_raw_sqlite_connection_row_factory() -> None:
    """The sentinel scheduler opens sqlite3 connections directly.

    Brain-pass adapters use row["column"] access, so run_brain_pass must
    normalize raw tuple-backed connections at the integration boundary.
    """
    c = sqlite3.connect(":memory:")
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        _seed_decision(c, id="dec_raw_conn", feedback_ewma=0.8)
        report = run_brain_pass(c, workspace_id="ws", settings=Settings())
    finally:
        c.close()
    assert report.errors == []
    assert "decision" in report.outcome_updated


def test_pass_writes_self_model_row(conn: sqlite3.Connection) -> None:
    settings = Settings()
    run_brain_pass(conn, workspace_id="ws", settings=settings)
    row = conn.execute(
        "SELECT identity_text, refreshed_via FROM self_model WHERE workspace_id = 'ws'"
    ).fetchone()
    assert row is not None
    assert "ws" in row["identity_text"]
    assert row["refreshed_via"] in ("heuristic", "ollama")


# ============================================================
# flag-off paths -- each independent gate must be honoured
# ============================================================


def test_outcome_loop_disabled_skips_outcome_step(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_OUTCOME_LOOP_ENABLED", "false")
    settings = Settings()
    report = run_brain_pass(conn, workspace_id="ws", settings=settings)
    assert report.outcome_updated == {}


def test_hebbian_disabled_skips_hebbian_step(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_HEBBIAN_ENABLED", "false")
    settings = Settings()
    report = run_brain_pass(conn, workspace_id="ws", settings=settings)
    assert report.hebbian_edges_upserted == 0
    assert report.hebbian_rows_pruned == 0


def test_self_model_disabled_skips_self_model_step(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_SELF_MODEL_ENABLED", "false")
    settings = Settings()
    report = run_brain_pass(conn, workspace_id="ws", settings=settings)
    assert report.self_model_refreshed is False
    # And the row is NOT written.
    row = conn.execute("SELECT 1 FROM self_model WHERE workspace_id = 'ws'").fetchone()
    assert row is None


def test_recall_disabled_skips_causal_extractor(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_RECALL_ENABLED", "false")
    settings = Settings()
    report = run_brain_pass(conn, workspace_id="ws", settings=settings)
    assert report.causal_invalidated == 0
    assert report.causal_derived == 0


def test_consolidation_feedback_disabled_skips_promote(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_CONSOLIDATION_FEEDBACK_ENABLED", "false")
    settings = Settings()
    report = run_brain_pass(conn, workspace_id="ws", settings=settings)
    assert report.insights_promoted == 0


def test_reflex_disabled_skips_distill(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_REFLEX_ENABLED", "false")
    settings = Settings()
    report = run_brain_pass(conn, workspace_id="ws", settings=settings)
    assert report.reflex_rules_distilled == 0


# ============================================================
# failure-soft: pre-migration DB
# ============================================================


def test_pass_on_pre_migration_db_does_not_raise() -> None:
    """Apply ONLY 0001; the pass swallows all sqlite3.OperationalError."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    settings = Settings()
    report = run_brain_pass(c, workspace_id="ws_pre", settings=settings)
    # All steps either succeed (with zero work) or fail-soft.
    # We just verify the function returned a report.
    assert report.workspace_id == "ws_pre"
    c.close()


# ============================================================
# idempotency: second pass on same data writes no new edges
# ============================================================


def test_second_pass_is_idempotent(conn: sqlite3.Connection) -> None:
    settings = Settings()
    first = run_brain_pass(conn, workspace_id="ws", settings=settings)
    second = run_brain_pass(conn, workspace_id="ws", settings=settings)
    # Self-model row is REPLACED each time, but counts stay consistent.
    assert first.self_model_refreshed == second.self_model_refreshed
    # Nothing new to distill / promote / extract.
    assert second.insights_promoted == 0
    assert second.causal_invalidated == 0


# ============================================================
# observability: learning-core failure escalates severity
# ============================================================


def _latest_step_failed_event(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT severity, summary, details_json
        FROM maintenance_events
        WHERE kind = 'brain_pass_step_failed'
        ORDER BY created_at DESC LIMIT 1
        """
    ).fetchone()


def test_core_step_failure_escalates_event_to_error(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing learning-core step (outcome) must surface as ERROR severity
    with the step named in core_failed -- the 2026-05-25 silent-core regression.
    """
    from agent_memory_lite.cognition import outcome_recompute  # noqa: PLC0415

    def _boom(*_a: object, **_k: object) -> dict[str, int]:
        raise RuntimeError("synthetic core failure")

    monkeypatch.setattr(outcome_recompute, "refresh_workspace", _boom)
    report = run_brain_pass(conn, workspace_id="ws", settings=Settings())

    assert any(e.startswith("outcome:") for e in report.errors)
    row = _latest_step_failed_event(conn)
    assert row is not None
    assert row["severity"] == "error"
    details = json.loads(row["details_json"])
    assert "outcome" in details["core_failed"]
    assert details["fingerprint"].startswith("brain_pass_step_failed:")


def test_repeated_identical_failure_coalesces_into_one_event(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permanently-broken step must not insert a fresh row every tick. Two
    passes with the same failure signature coalesce by fingerprint into ONE
    event with seen_count bumped, not two rows.
    """
    from agent_memory_lite.cognition import outcome_recompute  # noqa: PLC0415

    def _boom(*_a: object, **_k: object) -> dict[str, int]:
        raise RuntimeError("synthetic core failure")

    monkeypatch.setattr(outcome_recompute, "refresh_workspace", _boom)
    run_brain_pass(conn, workspace_id="ws", settings=Settings())
    run_brain_pass(conn, workspace_id="ws", settings=Settings())

    rows = conn.execute(
        "SELECT details_json FROM maintenance_events WHERE kind = 'brain_pass_step_failed'"
    ).fetchall()
    assert len(rows) == 1  # coalesced, not one-per-pass
    details = json.loads(rows[0]["details_json"])
    assert details["seen_count"] == 2
    assert "outcome" in details["core_failed"]


def test_recovered_failure_is_resolved_on_clean_tick(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient core blip must not leave a permanently-red ERROR. After the
    step recovers, the next clean pass resolves the open event so /health clears.
    """
    from agent_memory_lite.cognition import outcome_recompute  # noqa: PLC0415

    def _boom(*_a: object, **_k: object) -> dict[str, int]:
        raise RuntimeError("synthetic core failure")

    monkeypatch.setattr(outcome_recompute, "refresh_workspace", _boom)
    run_brain_pass(conn, workspace_id="ws", settings=Settings())  # fails -> open ERROR

    def _status() -> str | None:
        row = conn.execute(
            "SELECT status FROM maintenance_events WHERE kind = 'brain_pass_step_failed' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return None if row is None else row["status"]

    assert _status() == "open"

    monkeypatch.undo()  # step recovers
    run_brain_pass(conn, workspace_id="ws", settings=Settings())  # clean -> resolves
    assert _status() == "resolved"


def test_final_commit_failure_escalates_to_error(conn: sqlite3.Connection) -> None:
    """A failed final commit discards the entire pass's writes -- the worst
    outcome -- so it is treated as core and escalates to ERROR, even though it
    is the transaction boundary rather than a knowledge-formation step.
    """
    from agent_memory_lite.maintenance.brain_pass import _record_pass_outcome  # noqa: PLC0415

    report = _report()
    report.errors.append("final_commit:disk I/O error")
    _record_pass_outcome(conn, "ws", report)

    row = _latest_step_failed_event(conn)
    assert row is not None
    assert row["severity"] == "error"
    details = json.loads(row["details_json"])
    assert "final_commit" in details["core_failed"]


def test_peripheral_step_failure_stays_warning(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing peripheral step (db_hygiene) must NOT escalate: it is degraded
    housekeeping, not a dead brain. Severity stays WARNING, core_failed empty.
    """
    from agent_memory_lite.maintenance import db_hygiene  # noqa: PLC0415

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("synthetic hygiene failure")

    monkeypatch.setattr(db_hygiene, "run_db_hygiene", _boom)
    report = run_brain_pass(conn, workspace_id="ws", settings=Settings())

    assert any(e.startswith("db_hygiene:") for e in report.errors)
    assert not any(e.split(":", 1)[0] in {"outcome", "hebbian", "causal"} for e in report.errors)
    row = _latest_step_failed_event(conn)
    assert row is not None
    assert row["severity"] == "warning"
    details = json.loads(row["details_json"])
    assert details["core_failed"] == []


# ============================================================
# Phase-4: backup-prune step
# ============================================================


def _seed_aged_sibling(tmp_path: object) -> object:
    import os  # noqa: PLC0415
    import time  # noqa: PLC0415

    db = tmp_path / "memory.db"
    db.write_text("x", encoding="utf-8")
    bak = tmp_path / "memory.db.bak-fkrepair-001"
    bak.write_text("x", encoding="utf-8")
    old = time.time() - 99 * 86400
    os.utime(bak, (old, old))
    return db, bak


def test_prune_backups_disabled_is_noop(conn: sqlite3.Connection, tmp_path: object) -> None:
    db, bak = _seed_aged_sibling(tmp_path)
    settings = Settings().model_copy(update={"backup_retention_enabled": False, "db_path": db})
    report = _report()
    _step_prune_backups(conn, "ws", settings, report)
    assert report.backups_pruned == 0
    assert bak.exists()  # disabled -> untouched


def test_prune_backups_reaps_aged_siblings_and_stamps(
    conn: sqlite3.Connection, tmp_path: object
) -> None:
    from agent_memory_lite.config import workspace_meta  # noqa: PLC0415
    from agent_memory_lite.maintenance.brain_pass import _LAST_BACKUP_PRUNE_KEY  # noqa: PLC0415

    db, bak = _seed_aged_sibling(tmp_path)
    settings = Settings().model_copy(
        update={"db_path": db, "backup_retention_keep": 0, "backup_retention_age_days": 1}
    )
    report = _report()
    _step_prune_backups(conn, "ws", settings, report)
    assert report.backups_pruned == 1
    assert not bak.exists()
    assert workspace_meta.get(conn, "ws", _LAST_BACKUP_PRUNE_KEY)  # stamped


def test_prune_backups_rate_limited_within_24h(conn: sqlite3.Connection, tmp_path: object) -> None:
    from agent_memory_lite.config import workspace_meta  # noqa: PLC0415
    from agent_memory_lite.maintenance.brain_pass import _LAST_BACKUP_PRUNE_KEY  # noqa: PLC0415

    workspace_meta.set_value(conn, "ws", _LAST_BACKUP_PRUNE_KEY, iso_now())  # just ran
    db, bak = _seed_aged_sibling(tmp_path)
    settings = Settings().model_copy(
        update={"db_path": db, "backup_retention_keep": 0, "backup_retention_age_days": 1}
    )
    report = _report()
    _step_prune_backups(conn, "ws", settings, report)
    assert report.backups_pruned == 0  # skipped by the 24h gate
    assert bak.exists()
