"""Phase 04: theories + evidence + experiments + insights + concepts + snapshots."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post, seed_theories

from agent_memory_lite.ingestion.theory_evidence_writer import add_theory_evidence
from agent_memory_lite.models.theories import TheoryEvidenceIn


class P04Theories(Phase):
    name = "p04_theories"
    description = "Theory + evidence + canonical research memory surface."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        theory_ids = seed_theories(state.client, workspace_id=state.workspace_id)
        state.bag["theory_ids"] = theory_ids
        result.assert_eq("three theories written", len(theory_ids), 3)

        # Add an evidence row to the validated theory (theory_ids[1]).
        ev = add_theory_evidence(
            state.conn,
            TheoryEvidenceIn(
                workspace_id=state.workspace_id,
                theory_id=theory_ids[1],
                kind="supporting",
                summary="QA fixture: validation criteria met by replay run",
                metrics={"n": 42, "score": 0.9},
                confidence=0.85,
            ),
        )
        state.bag["evidence_id"] = ev.id
        result.assert_true("evidence_id returned", bool(ev.id), hint=str(ev)[:120])

        # Snapshot (research dataset, not memory state).
        snap = post(
            state.client,
            "/memory/register_snapshot",
            {
                "workspace_id": state.workspace_id,
                "snapshot_key": "qa-test-snapshot-1",
                "title": "QA fixture snapshot",
                "source": "test",
                "table_counts": {"episodes": 3},
                "total_rows": 3,
            },
        )
        state.bag["snapshot_id"] = snap.get("snapshot_id")
        result.assert_true("snapshot_id returned", bool(snap.get("snapshot_id")))

        # Experiment + result.
        exp = post(
            state.client,
            "/memory/write_experiment",
            {
                "workspace_id": state.workspace_id,
                "theory_id": theory_ids[0],
                "snapshot_id": snap.get("snapshot_id"),
                "title": "QA replay",
                "hypothesis": "Replay reproduces the fixture metric.",
                "cohort_definition": "all qa episodes",
                "success_criteria": {"min_trades": 1},
                "command": "echo qa",
                "priority": 0.5,
            },
        )
        state.bag["experiment_id"] = exp.get("experiment_id")
        result.assert_true("experiment_id returned", bool(exp.get("experiment_id")))

        post(
            state.client,
            "/memory/add_experiment_result",
            {
                "workspace_id": state.workspace_id,
                "experiment_id": exp.get("experiment_id"),
                "kind": "supporting",
                "summary": "Reproduced the fixture metric within tolerance",
                "metrics": {"n": 42, "delta": 0.01},
                "confidence": 0.8,
            },
        )

        # Concept + insight.
        concept = post(
            state.client,
            "/memory/upsert_concept",
            {
                "workspace_id": state.workspace_id,
                "name": "qa-replay-window",
                "kind": "metric",
                "definition": "The replay window used by the QA fixture.",
            },
        )
        result.assert_true("concept written", bool(concept.get("concept_id")))

        insight = post(
            state.client,
            "/memory/distill_insight",
            {
                "workspace_id": state.workspace_id,
                "insight_type": "lesson",
                "summary": "The crash test confirms research-lab endpoints respond.",
                "target_type": "theory",
                "target_id": theory_ids[1],
                "confidence": 0.7,
            },
        )
        state.bag["insight_id"] = insight.get("insight_id")
        result.assert_true("insight written", bool(insight.get("insight_id")))

        theory_search = post(
            state.client,
            "/memory/search",
            {
                "workspace_id": state.workspace_id,
                "query": "qa replay validation",
                "kinds": ["theory", "insight", "concept"],
                "limit": 10,
            },
        )
        result.assert_true(
            "canonical research search returns theory/insight/concept",
            bool(theory_search.get("data")) and isinstance(theory_search, dict),
        )
        return result
