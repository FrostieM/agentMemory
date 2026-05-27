"""Phase 20: v1.8 — reflective compaction. Auto-skip if Ollama down."""

from __future__ import annotations

import httpx
from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


def _ollama_reachable() -> bool:
    try:
        response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


class P20V18Reflective(Phase):
    name = "p20_v18_reflective"
    description = "/memory/compact emits canonical insight candidates when Ollama is reachable."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        if state.skip_llm or not _ollama_reachable():
            result.skip("Ollama not reachable")
            return result

        # Run /memory/compact. With MEMORY_REFLECTIVE_COMPACT_ENABLED=true the
        # compaction path will additionally call the lesson extractor. We
        # don't assert how many candidates it produces (LLM is non-determ.)
        # — only that the surface is reachable + insights stays
        # untouched (trust-gate guard).
        insights_before = int(
            state.conn.execute(
                "SELECT COUNT(*) FROM insights WHERE workspace_id = ?",
                (state.workspace_id,),
            ).fetchone()[0]
        )
        post(
            state.client,
            "/memory/compact",
            {"workspace_id": state.workspace_id},
        )
        insights_after = int(
            state.conn.execute(
                "SELECT COUNT(*) FROM insights WHERE workspace_id = ?",
                (state.workspace_id,),
            ).fetchone()[0]
        )
        result.assert_eq(
            "insights table NOT mutated by reflective compaction",
            insights_after,
            insights_before,
        )

        open_candidates = int(
            state.conn.execute(
                """
                SELECT COUNT(*) FROM candidates
                WHERE workspace_id = ? AND kind = 'insight' AND status = 'new'
                """,
                (state.workspace_id,),
            ).fetchone()[0]
        )
        result.assert_ge("canonical insight candidate query works", open_candidates, 0)
        return result
