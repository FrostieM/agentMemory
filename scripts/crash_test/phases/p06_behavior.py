"""Phase 06: canonical behaviors + governance fields + listing."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get, post, seed_behavior_instruction


class P06Behavior(Phase):
    name = "p06_behavior"
    description = "Write + list + governance fields appear in compact brief."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        instr_id = seed_behavior_instruction(state.client, workspace_id=state.workspace_id)
        state.bag["behavior_id"] = instr_id
        result.assert_true("instruction id returned", bool(instr_id))
        pin = post(
            state.client,
            "/memory/pin",
            {
                "workspace_id": state.workspace_id,
                "kind": "behavior",
                "id": instr_id,
                "pinned": True,
            },
        )
        result.assert_true("pin behavior returns ok", bool(pin.get("ok", pin)))

        listed_response = state.client.post(
            "/memory/search",
            json={
                "workspace_id": state.workspace_id,
                "kinds": ["behavior"],
                "query": "Crash-test reporting style",
                "limit": 10,
            },
            timeout=30.0,
        )
        listed_response.raise_for_status()
        items = [
            item.get("projection", {})
            for item in (listed_response.json().get("data") or [])
            if isinstance(item, dict)
        ]
        result.assert_ge("listed instructions", len(items), 1)
        first = items[0] if items else {}
        detail = (
            get(
                state.client,
                "/memory/get",
                {
                    "workspace_id": state.workspace_id,
                    "kind": "behavior",
                    "id": first.get("id"),
                    "fields": "kind,conflict_policy",
                },
            ).get("data")
            or {}
        )
        result.assert_eq("kind", detail.get("kind"), "communication_style")
        result.assert_eq("conflict_policy", detail.get("conflict_policy"), "current_user_wins")

        brief = get(
            state.client,
            "/memory/brief",
            {
                "workspace_id": state.workspace_id,
                "task": "incident report",
                "max_tokens": 2000,
            },
        )
        data = brief.get("data") if isinstance(brief.get("data"), dict) else {}
        result.assert_in(
            "brief mentions behavior", "Crash-test reporting style", str(data.get("body_md", ""))
        )
        return result
