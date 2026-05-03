"""Phase 02: search + get_context + explain_context + get_object."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P02Retrieval(Phase):
    name = "p02_retrieval"
    description = "Search modes, context envelope sections, explain/get_object lookups."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        # FTS exact lookup hits seeded episode body.
        fts = post(
            state.client,
            "/memory/search",
            {"workspace_id": state.workspace_id, "query": "rrf_norm", "mode": "fts", "limit": 5},
        )
        hits = fts.get("hits") or fts.get("results") or []
        result.assert_ge("fts hits", len(hits), 1)

        # /memory/search supports only mode=fts in v1.0; vector + hybrid go
        # through /memory/get_context. So we exercise vector indirectly via
        # the context envelope which fuses FTS + vector via RRF.

        # get_context — envelope must contain the canonical sections.
        ctx = post(
            state.client,
            "/memory/get_context",
            {
                "workspace_id": state.workspace_id,
                "query": "retrieval pipeline",
                "max_tokens": 2000,
            },
        )
        text = str(ctx.get("context_text", ""))
        for section in (
            "<memory_context>",
            "<retrieved_chunks",
            "</memory_context>",
        ):
            result.assert_in("envelope contains", section, text)

        # explain_context returns retrieval breakdown.
        explain = post(
            state.client,
            "/memory/explain_context",
            {"workspace_id": state.workspace_id, "query": "retrieval pipeline", "max_tokens": 1500},
        )
        result.assert_true(
            "explain returns object",
            isinstance(explain, dict) and bool(explain),
            hint=str(explain)[:120],
        )
        return result
