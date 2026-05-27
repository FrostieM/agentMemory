"""Phase 02: compact search + brief + object fetch."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get, post


class P02Retrieval(Phase):
    name = "p02_retrieval"
    description = "Compact search, brief rendering, and direct object lookup."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        search = post(
            state.client,
            "/memory/search",
            {"workspace_id": state.workspace_id, "query": "rrf_norm", "limit": 5},
        )
        hits = search.get("data") or search.get("hits") or search.get("results") or []
        result.assert_ge("search hits", len(hits), 1)

        brief = get(
            state.client,
            "/memory/brief",
            {
                "workspace_id": state.workspace_id,
                "task": "retrieval pipeline",
                "max_tokens": 2000,
            },
        )
        data = brief.get("data") if isinstance(brief.get("data"), dict) else {}
        result.assert_true("brief returned body", bool(str(data.get("body_md", "")).strip()))

        first = hits[0].get("projection", hits[0]) if isinstance(hits[0], dict) else {}
        if isinstance(first, dict) and first.get("id"):
            fetched = get(
                state.client,
                "/memory/get",
                {
                    "workspace_id": state.workspace_id,
                    "kind": first.get("kind", "chunk"),
                    "id": first["id"],
                },
            )
            result.assert_true("direct get returns data", bool(fetched.get("data")))
        return result
