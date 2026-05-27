"""Phase 05: canonical skills + capability suggestions."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post, seed_capabilities


class P05Capabilities(Phase):
    name = "p05_capabilities"
    description = "Canonical skill write/search plus theory capability suggestions."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        ids = seed_capabilities(state.client, workspace_id=state.workspace_id)
        state.bag["capability_ids"] = ids
        for key in ("role_id", "skill_id", "playbook_id"):
            result.assert_true(f"{key} returned", bool(ids[key]))

        # Compact search surfaces the canonical skill row.
        listed = post(
            state.client,
            "/memory/search",
            {
                "workspace_id": state.workspace_id,
                "query": "End-to-end memory verification",
                "kinds": ["skill"],
                "limit": 10,
            },
        )
        skills = listed.get("data") or listed.get("hits") or []
        result.assert_ge("listed skills", len(skills), 1)

        theory = post(
            state.client,
            "/memory/write",
            {
                "workspace_id": state.workspace_id,
                "kind": "theory",
                "payload": {
                    "title": "End-to-end memory verification coverage",
                    "claim": "Canonical endpoint changes need end-to-end verification.",
                    "status": "testing",
                    "confidence": 0.55,
                },
            },
        )
        suggestions = theory.get("data", {}).get("capability_suggestions") or []
        result.assert_ge("theory has capability suggestions", len(suggestions), 1)
        result.assert_eq(
            "top suggestion is canonical skill",
            suggestions[0].get("capability_name"),
            "End-to-end memory verification",
        )
        return result
