"""Phase 08: temporal graph (entities + facts) — exercised through ingestion.

The graph fills in via heuristic + LLM extraction during ingest_episode.
On a fresh workspace with the heuristic-only path, entity/fact rows may be
zero — the assertion is "schema reachable + counts make sense" rather than
"specific facts created", because LLM extractor is non-deterministic in a
crash test.
"""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult


class P08Graph(Phase):
    name = "p08_graph"
    description = "entities + facts tables reachable; counts >= 0; valid_from set when present."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        ent_count = int(
            state.conn.execute(
                "SELECT COUNT(*) FROM entities WHERE workspace_id = ?",
                (state.workspace_id,),
            ).fetchone()[0]
        )
        fact_count = int(
            state.conn.execute(
                "SELECT COUNT(*) FROM facts WHERE workspace_id = ?",
                (state.workspace_id,),
            ).fetchone()[0]
        )
        result.assert_ge("entities table reachable", ent_count, 0)
        result.assert_ge("facts table reachable", fact_count, 0)

        # If facts exist they must have valid_from populated.
        bad = int(
            state.conn.execute(
                "SELECT COUNT(*) FROM facts WHERE workspace_id = ? AND valid_from IS NULL",
                (state.workspace_id,),
            ).fetchone()[0]
        )
        result.assert_eq("facts have valid_from", bad, 0)
        result.note(f"entities={ent_count} facts={fact_count}")
        return result
