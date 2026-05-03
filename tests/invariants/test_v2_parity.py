"""Flag-off parity invariant for 1.1.0 (97d30fa).

Locks in the contract: with all three v2 flags OFF, the system behaves
byte-identically to v1.9. Future edits that accidentally couple v2 logic
to the always-on path will break this test.

Three checks, one per v2 module:
* implicit_feedback: archive/promote/link helpers are no-ops without flag
* pending_review: envelope is byte-equal to input when load_pending_review
  returns empty (no candidates) — i.e. the injection path is data-driven,
  not flag-driven, but still off-by-default in absence of data
* sentinel_scheduler: maybe_run_sentinels returns False with hours=0
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.routes.context_post_build import inject_pending_review
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.implicit_feedback import (
    record_implicit_archive,
    record_implicit_link,
    record_implicit_promote,
)
from agent_memory_lite.maintenance.sentinel_scheduler import maybe_run_sentinels


def _v2_flags_off() -> Settings:
    """Settings object with every v2 flag explicitly off."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_IMPLICIT_FEEDBACK_ENABLED="false",
        MEMORY_SENTINEL_AUTORUN_HOURS="0.0",
    )


def test_implicit_feedback_off_writes_nothing(applied_conn: sqlite3.Connection) -> None:
    settings = _v2_flags_off()
    before = applied_conn.execute("SELECT COUNT(*) FROM memory_usage_feedback").fetchone()[0]

    assert (
        record_implicit_archive(
            applied_conn,
            settings=settings,
            workspace_id="default",
            source_type="chunk",
            source_id="chk_x",
        )
        is False
    )
    assert (
        record_implicit_promote(
            applied_conn,
            settings=settings,
            workspace_id="default",
            source_type="decision",
            source_id="dec_x",
        )
        is False
    )
    assert (
        record_implicit_link(
            applied_conn,
            settings=settings,
            workspace_id="default",
            target_type="theory",
            target_id="th_x",
            strength=0.9,
        )
        is False
    )

    after = applied_conn.execute("SELECT COUNT(*) FROM memory_usage_feedback").fetchone()[0]
    assert before == after, "v2 implicit feedback wrote rows with flag off"


def test_pending_review_envelope_unchanged_when_no_candidates(
    applied_conn: sqlite3.Connection,
) -> None:
    """No pending candidates → injected envelope is byte-equal to original."""
    envelope_in = "<memory_context>\n  <core_memory/>\n  <retrieved_chunks/>\n</memory_context>"
    envelope_out = inject_pending_review(
        applied_conn, workspace_id="default", envelope_text=envelope_in
    )
    assert envelope_out == envelope_in, "pending_review injected without source data"


def test_sentinel_scheduler_off_returns_false(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Hours=0 → maybe_run_sentinels never schedules."""
    settings = _v2_flags_off()
    db_file = tmp_path / "ws.db"
    db_file.touch()
    triggered = maybe_run_sentinels(
        workspace_id="default",
        settings=settings,
        db_path=str(db_file),
        embedding_provider=None,
        vector_store=None,
    )
    assert triggered is False
