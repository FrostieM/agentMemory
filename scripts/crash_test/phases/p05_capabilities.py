"""Phase 05: agent_roles / agent_skills / agent_playbooks + capability_links."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post, seed_capabilities


class P05Capabilities(Phase):
    name = "p05_capabilities"
    description = "Capability upserts + list + capability_links to research targets."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        ids = seed_capabilities(state.client, workspace_id=state.workspace_id)
        state.bag["capability_ids"] = ids
        for key in ("role_id", "skill_id", "playbook_id"):
            result.assert_true(f"{key} returned", bool(ids[key]))

        # List endpoint surfaces all three.
        listed = post(
            state.client,
            "/memory/list_agent_capabilities",
            {"workspace_id": state.workspace_id, "limit": 10},
        )
        roles = listed.get("roles") or []
        skills = listed.get("skills") or []
        playbooks = listed.get("playbooks") or []
        result.assert_ge("listed roles", len(roles), 1)
        result.assert_ge("listed skills", len(skills), 1)
        result.assert_ge("listed playbooks", len(playbooks), 1)

        # capability_links: link skill -> a theory we seeded earlier.
        theory_ids = state.bag.get("theory_ids") or []
        if theory_ids:
            link = post(
                state.client,
                "/memory/link_capability",
                {
                    "workspace_id": state.workspace_id,
                    "target_type": "theory",
                    "target_id": theory_ids[0],
                    "capability_type": "skill",
                    "capability_name": "End-to-end memory verification",
                    "relation": "method",
                    "rationale": "QA fixture validates retrieval end to end.",
                    "strength": 0.8,
                },
            )
            result.assert_true(
                "capability_link created", bool(link.get("link_id") or link.get("id"))
            )
            listed_links = post(
                state.client,
                "/memory/list_capability_links",
                {
                    "workspace_id": state.workspace_id,
                    "target_type": "theory",
                    "target_id": theory_ids[0],
                    "limit": 10,
                },
            )
            result.assert_ge("links listed", len(listed_links.get("links") or []), 1)
        else:
            result.note("no theory ids in bag, skipped link assertions")
        return result
