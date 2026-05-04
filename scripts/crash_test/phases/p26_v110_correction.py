"""Phase 26: v1.10 correction-aware learning loop end-to-end.

Synthesises a (claim, correction) episode pair, asserts the
CorrectionExtractor surfaces a CORRECTION-kind memory_candidate,
promotes it through /memory/promote_candidate_to_behavior, and
verifies the resulting behavior_instruction lands in the next
get_context envelope.
"""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P26V110Correction(Phase):
    name = "p26_v110_correction"
    description = (
        "v1.10: claim+correction pair → memory_candidate(kind=correction) → "
        "promote_candidate_to_behavior → behavior_instruction visible in envelope."
    )

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        ws = state.workspace_id

        # 1. Ingest the agent claim.
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

        # 2. Ingest the user correction with cross-reference metadata.
        post(
            state.client,
            "/memory/ingest_episode",
            {
                "workspace_id": ws,
                "source_type": "user_message",
                "raw_text": (
                    "нет, MCP только что был запущен после релиза 1.1.0. "
                    "audit_log нужно фильтровать по дате релиза прежде чем "
                    "делать выводы о состоянии хука."
                ),
                "trust_level": "user_asserted",
                "importance": 0.85,
                "metadata": {
                    "kind": "user_correction",
                    "correction_target_episode_id": claim_id,
                },
            },
        )

        # 3. memory_candidate(kind=CORRECTION) should now exist.
        listed = post(
            state.client,
            "/memory/list_candidates",
            {"workspace_id": ws, "statuses": ["new"], "limit": 20},
        )
        corrections = [c for c in listed.get("candidates", []) if c.get("kind") == "correction"]
        result.assert_ge("CORRECTION candidate emitted", len(corrections), 1)
        if not corrections:
            return result
        cand = corrections[0]
        cand_id = cand["candidate_id"]

        # 4. Pending review envelope surfaces the correction.
        ctx = post(
            state.client,
            "/memory/get_context",
            {"workspace_id": ws, "query": "any", "max_tokens": 2000},
        )
        text = ctx.get("context_text", "")
        result.assert_true(
            "envelope includes <pending_review>",
            "<pending_review" in text,
        )
        result.assert_true(
            "correction kind surfaced in pending_review",
            'kind="correction_candidate"' in text,
        )
        result.assert_true(
            "promote endpoint hint surfaced",
            "promote_candidate_to_behavior" in text,
        )

        # 5. Promote with a clean rule + name.
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
        bi_id = promoted.get("behavior_instruction_id") or ""
        result.assert_true("behavior_instruction written", bool(bi_id))
        result.assert_eq("promotion status", promoted.get("status"), "promoted")

        # 6. New context surfaces the behavior_instruction.
        ctx2 = post(
            state.client,
            "/memory/get_context",
            {"workspace_id": ws, "query": "any", "max_tokens": 2000},
        )
        text2 = ctx2.get("context_text", "")
        result.assert_true(
            "behavior_instruction visible in next envelope",
            "v110-crash-test-rule" in text2 or "release_date" in text2,
        )

        # 7. Candidate flipped to promoted with target lineage filled.
        promoted_listed = post(
            state.client,
            "/memory/list_candidates",
            {"workspace_id": ws, "statuses": ["promoted"], "limit": 5},
        )
        match = [
            c for c in promoted_listed.get("candidates", []) if c.get("candidate_id") == cand_id
        ]
        result.assert_ge("promoted candidate found", len(match), 1)
        if match:
            result.assert_eq(
                "promoted_target_type",
                match[0].get("promoted_target_type"),
                "behavior_instruction",
            )
            result.assert_eq(
                "promoted_target_id matches behavior id",
                match[0].get("promoted_target_id"),
                bi_id,
            )

        return result
