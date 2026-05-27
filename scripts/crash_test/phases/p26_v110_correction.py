"""Phase 26: correction-aware learning loop end-to-end.

Synthesises a claim/correction episode pair, asserts the extractor surfaces a
correction candidate, promotes it to a behavior, and verifies compact search can
retrieve the promoted behavior.
"""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P26V110Correction(Phase):
    name = "p26_v110_correction"
    description = "claim+correction pair -> correction candidate -> promoted behavior."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        ws = state.workspace_id

        claim_resp = post(
            state.client,
            "/memory/ingest_episode",
            {
                "workspace_id": ws,
                "source_type": "agent_action",
                "raw_text": (
                    "the implicit feedback loop is broken because the "
                    "audit_log shows 1424 entries but only 1 feedback row"
                ),
                "trust_level": "agent_observed",
                "importance": 0.5,
                "metadata": {"kind": "correction_target"},
            },
        )
        claim_id = claim_resp.get("episode_id") or ""
        result.assert_true("claim episode written", bool(claim_id))
        if not claim_id:
            return result

        post(
            state.client,
            "/memory/ingest_episode",
            {
                "workspace_id": ws,
                "source_type": "user_message",
                "raw_text": (
                    "нет, MCP только что был запущен после релиза 1.1.0. "
                    "audit_log нужно фильтровать по дате релиза прежде чем "
                    "делать выводы о состоянии hook."
                ),
                "trust_level": "user_asserted",
                "importance": 0.85,
                "metadata": {
                    "kind": "user_correction",
                    "correction_target_episode_id": claim_id,
                },
            },
        )

        listed = post(
            state.client,
            "/memory/review_queue",
            {"workspace_id": ws, "limit_per_kind": 20},
        )
        corrections = [
            item
            for item in listed.get("items", [])
            if item.get("target_type") == "candidate"
            and (item.get("details") or {}).get("kind") == "correction"
        ]
        result.assert_ge("CORRECTION candidate emitted", len(corrections), 1)
        if not corrections:
            return result
        cand_id = corrections[0]["target_id"]

        promoted = post(
            state.client,
            "/memory/promote_candidate_to_behavior",
            {
                "workspace_id": ws,
                "candidate_id": cand_id,
                "name": "v110-crash-test-rule",
                "rule_text_override": (
                    "Filter audit_log by created_at > release_date before "
                    "claiming a feature is dormant."
                ),
                "decided_by": "crash-test",
            },
        )
        behavior_id = promoted.get("behavior_id") or promoted.get("behavior_instruction_id") or ""
        result.assert_true("behavior written", bool(behavior_id))
        result.assert_eq("promotion status", promoted.get("status"), "promoted")

        search = post(
            state.client,
            "/memory/search",
            {"workspace_id": ws, "query": "release_date", "kinds": ["behavior"], "limit": 5},
        )
        result.assert_true(
            "behavior visible in compact search",
            "v110-crash-test-rule" in str(search) or "release_date" in str(search),
        )

        fetched = state.client.get(
            "/memory/get",
            params={"workspace_id": ws, "kind": "behavior", "id": behavior_id},
            timeout=30.0,
        )
        fetched.raise_for_status()
        result.assert_eq("promoted behavior fetches", fetched.json()["data"]["id"], behavior_id)

        return result
