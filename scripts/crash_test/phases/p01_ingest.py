"""Phase 01: ingest_episode + ingest_file."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post, seed_episodes


class P01Ingest(Phase):
    name = "p01_ingest"
    description = "Episodes write through to chunks + FTS + vector; files are idempotent."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        episode_ids = seed_episodes(state.client, workspace_id=state.workspace_id, count=3)
        state.bag["episode_ids"] = episode_ids
        result.assert_eq("episodes inserted", len(episode_ids), 3)
        chunks = state.conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE workspace_id = ?", (state.workspace_id,)
        ).fetchone()[0]
        result.assert_ge("chunks created", int(chunks), 3)
        fts = state.conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE workspace_id = ?", (state.workspace_id,)
        ).fetchone()[0]
        result.assert_ge("chunks_fts rows", int(fts), 3)

        # File ingest, twice — second call must skip on content_hash.
        first = post(
            state.client,
            "/memory/ingest_file",
            {
                "workspace_id": state.workspace_id,
                "path": "qa/notes.md",
                "content": "# QA\nThis file documents the crash test corpus.",
                "language": "markdown",
            },
        )
        second = post(
            state.client,
            "/memory/ingest_file",
            {
                "workspace_id": state.workspace_id,
                "path": "qa/notes.md",
                "content": "# QA\nThis file documents the crash test corpus.",
                "language": "markdown",
            },
        )
        result.assert_eq("first ingest_file skipped", bool(first.get("skipped")), False)
        result.assert_eq("idempotent re-ingest skipped", bool(second.get("skipped")), True)
        state.bag["file_id"] = first.get("file_id")
        return result
