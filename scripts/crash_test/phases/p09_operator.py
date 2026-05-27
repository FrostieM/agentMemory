"""Phase 09: archive + pin + what_references + audit."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get, post


class P09Operator(Phase):
    name = "p09_operator"
    description = "Archive hides item, pin forces it into context, what_references / audit work."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        decision_ids = state.bag.get("decision_ids") or []
        if not decision_ids:
            result.skip("no decisions in bag")
            return result
        target = decision_ids[1]  # the active second decision

        # Pin the active decision; verify the canonical row reflects it.
        pin = post(
            state.client,
            "/memory/pin",
            {
                "workspace_id": state.workspace_id,
                "kind": "decision",
                "id": target,
                "pinned": True,
            },
        )
        result.assert_true("pin returns ok", bool(pin))
        detail = get(
            state.client,
            "/memory/get",
            {
                "workspace_id": state.workspace_id,
                "kind": "decision",
                "id": target,
                "fields": "pinned",
            },
        )
        data = detail.get("data") if isinstance(detail.get("data"), dict) else {}
        result.assert_true("pinned decision persisted", bool(data.get("pinned")))

        # what_references searches text columns via LIKE %target_id% — so we
        # plant the literal id in an episode raw_text (the search includes
        # episodes.raw_text) which is the cleanest fixture path.
        post(
            state.client,
            "/memory/ingest_episode",
            {
                "workspace_id": state.workspace_id,
                "session_id": "qa-refs",
                "task_id": "qa-task-1",
                "source_type": "agent_action",
                "raw_text": f"QA fixture episode mentioning decision {target}.",
                "trust_level": "agent_observed",
                "importance": 0.3,
            },
        )
        refs = post(
            state.client,
            "/memory/what_references",
            {"workspace_id": state.workspace_id, "target_id": target, "limit": 50},
        )
        # what_references can return under any of several keys depending on
        # the response model — accept whichever is present, including a
        # flat dict-of-lists shape.
        ref_count = sum(
            len(refs.get(k) or []) for k in ("references", "hits", "items", "rows", "results")
        )
        if ref_count == 0 and isinstance(refs, dict):
            for v in refs.values():
                if isinstance(v, list):
                    ref_count += len(v)
        result.assert_ge("what_references finds at least one referrer", ref_count, 1)

        # /memory/audit returns at least the write_decision row.
        audit = post(
            state.client,
            "/memory/audit",
            {
                "workspace_id": state.workspace_id,
                "target_type": "decision",
                "target_id": target,
                "limit": 20,
            },
        )
        rows = audit.get("rows") or audit.get("entries") or audit.get("audit") or []
        result.assert_ge("audit rows for decision", len(rows), 1)

        # Archive a chunk and verify the canonical row is marked archived.
        chunk_id_row = state.conn.execute(
            "SELECT id FROM chunks WHERE workspace_id = ? LIMIT 1", (state.workspace_id,)
        ).fetchone()
        if chunk_id_row is not None:
            chunk_id = str(chunk_id_row[0])
            post(
                state.client,
                "/memory/archive",
                {
                    "workspace_id": state.workspace_id,
                    "kind": "chunk",
                    "id": chunk_id,
                    "reason": "crash-test fixture",
                },
            )
            archived = state.conn.execute(
                "SELECT is_archived FROM chunks WHERE id = ?", (chunk_id,)
            ).fetchone()
            result.assert_eq("chunk archived", int(archived[0]), 1)
        return result
