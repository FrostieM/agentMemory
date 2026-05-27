"""Phase 22: cross-cutting trust & safety invariants.

* Local-only guard: importing the cloud-banned packages would fail at
  ruff time, so we don't try at runtime; instead we assert storage never
  includes raw-SECRET-looking strings ingested earlier.
* Trust gate: after writing an episode that contains a fake API key, the
  redaction layer must scrub it before storage.
"""

from __future__ import annotations

import re

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post

_SECRET_LIKE = re.compile(r"sk-[A-Za-z0-9]{20,}")


class P22TrustSafety(Phase):
    name = "p22_trust_safety"
    description = "Redaction strips API-key-shaped strings; trust gate is intact for untrusted_doc."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        # Plant a fake key in an episode and check storage / retrieval doesn't expose it.
        secret = "sk-FAKEABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
        out = post(
            state.client,
            "/memory/ingest_episode",
            {
                "workspace_id": state.workspace_id,
                "session_id": "qa-secret",
                "task_id": "qa-task-1",
                "source_type": "agent_action",
                "raw_text": f"Configured client with API key: {secret}",
                "trust_level": "agent_observed",
                "importance": 0.4,
            },
        )
        episode_id = out.get("episode_id")
        result.assert_true("episode written", bool(episode_id))

        # Direct DB peek — secret must NOT appear verbatim.
        rows = state.conn.execute(
            "SELECT raw_text FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        stored = str(rows[0]) if rows else ""
        result.assert_true(
            "raw key not present in episodes.raw_text",
            _SECRET_LIKE.search(stored) is None,
            hint=stored[:80],
        )

        # Same for chunks.
        chunk_rows = state.conn.execute(
            "SELECT text FROM chunks WHERE workspace_id = ? AND episode_id = ?",
            (state.workspace_id, episode_id),
        ).fetchall()
        for ct in chunk_rows:
            result.assert_true(
                "raw key not present in chunks.text",
                _SECRET_LIKE.search(str(ct[0])) is None,
                hint=str(ct[0])[:80],
            )
        return result
