from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.bootstrap.project_memory_seed import (
    PROFILE_NAME,
    seed_neutral_project_memory,
)


def _count(conn: sqlite3.Connection, table: str, workspace_id: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    assert row is not None
    return int(row["n"])


def test_neutral_project_memory_seed_writes_only_population_helpers(
    applied_conn: sqlite3.Connection,
) -> None:
    result = seed_neutral_project_memory(applied_conn, workspace_id="project-x")

    assert result.profile == PROFILE_NAME
    assert result.roles_written == 0
    # 1.2.3: seed wrote one capability-context discipline rule.
    # 1.2.4: added second rule — search-before-write discipline.
    # 2.2 (Phase 1.2 of v2.2 consolidation, 2026-05-10): added
    # memory-first-before-edit and no-unauthorized-git-push discipline
    # rules; the latter two are pinned by default.
    # 2.2.x (2026-05-13): added three cross-project enforcement rules —
    # applies-to-checklist-must-be-stated-verbatim,
    # verification-claims-must-cite-prod-evidence,
    # memory-write-is-not-done-until-candidates-resolved; all three
    # pinned. Total seed BIs = 7. All seeded BIs must be project-AGNOSTIC
    # (no language, personality, or project-specific behavior).
    # Project-specific rules remain operator-driven via memory_write(kind=behavior).
    assert result.behavior_instructions_written == 12
    assert [item.name for item in result.skills] == ["Memory population discipline"]
    assert [item.name for item in result.playbooks] == ["Neutral memory bootstrap"]
    assert {item.name for item in result.concepts} == {
        "workspace_id",
        "memory candidate review",
        "memory snapshot",
        "memory integrity audit",
    }
    assert {item.name for item in result.behavior_instructions} == {
        "Record capability suggestion after every decision and theory write",
        "Search before write - auto-inject is not exhaustive",
        "Memory-first before reading or editing source",
        "No git commit/push/CI without explicit operator permission",
        "applies-to-checklist-must-be-stated-verbatim",
        "verification-claims-must-cite-prod-evidence",
        "memory-write-is-not-done-until-candidates-resolved",
        "pretooluse:no-magic-number-in-strategy",
        "pretooluse:decision-must-have-provenance",
        "pretooluse:read-before-edit",
        "pretooluse:impact-check-before-read",
        "pretooluse:search-before-architectural-write",
    }

    assert _count(applied_conn, "behaviors", "project-x") == 12
    assert _count(applied_conn, "skills", "project-x") == 2
    assert (
        applied_conn.execute(
            "SELECT COUNT(*) FROM skills WHERE workspace_id='project-x' AND subtype='role'"
        ).fetchone()[0]
        == 0
    )
    assert _count(applied_conn, "concepts", "project-x") == 4


def test_seeded_skill_and_behavior_are_fts_searchable(applied_conn: sqlite3.Connection) -> None:
    """M1 (write-atomicity batch), end-to-end: project seeding writes durable skill
    + behavior rows via upsert_agent_skill / upsert_behavior_instruction, bypassing
    write_canonical's FTS choke point. The writer-level FTS sync closes this path so
    the discipline rules a freshly-registered agent relies on are immediately
    searchable. Before the fix they were silently invisible to memory_search until a
    brain-pass rebuild tick (a reported HIGH silent-loss bug)."""
    from agent_memory_lite.storage.reader import search_kind_fts  # noqa: PLC0415

    result = seed_neutral_project_memory(applied_conn, workspace_id="project-x")

    skill_id = result.skills[0].id  # "Memory population discipline"
    skill_hits = search_kind_fts(
        applied_conn,
        workspace_id="project-x",
        kind="skill",
        query="population discipline",
        limit=10,
    )
    assert skill_id in [h.projection["id"] for h in skill_hits]

    # R6 audit: the seeded playbook ("Neutral memory bootstrap") lives in the skills
    # table and must ALSO be FTS-searchable -- before the role/playbook sync fix it
    # was silently hidden behind the competing seeded skill (the LIKE fallback was
    # suppressed once any skill was FTS-indexed).
    playbook_id = result.playbooks[0].id
    pb_hits = search_kind_fts(
        applied_conn, workspace_id="project-x", kind="skill", query="neutral bootstrap", limit=10
    )
    assert playbook_id in [h.projection["id"] for h in pb_hits]

    target = next(
        b
        for b in result.behavior_instructions
        if b.name == "applies-to-checklist-must-be-stated-verbatim"
    )
    beh_hits = search_kind_fts(
        applied_conn, workspace_id="project-x", kind="behavior", query="verbatim", limit=20
    )
    assert target.id in [h.projection["id"] for h in beh_hits]


def test_neutral_project_memory_seed_is_idempotent(applied_conn: sqlite3.Connection) -> None:
    first = seed_neutral_project_memory(applied_conn, workspace_id="project-x")
    second = seed_neutral_project_memory(applied_conn, workspace_id="project-x")

    assert first.skills[0].id == second.skills[0].id
    assert first.playbooks[0].id == second.playbooks[0].id
    assert {item.id for item in first.concepts} == {item.id for item in second.concepts}
    # Behavior_instruction upsert is also idempotent on (workspace_id, name).
    # All N seeded BIs must round-trip with stable ids on re-seed.
    first_bi_ids = {item.id for item in first.behavior_instructions}
    second_bi_ids = {item.id for item in second.behavior_instructions}
    assert first_bi_ids == second_bi_ids
    assert _count(applied_conn, "skills", "project-x") == 2
    assert _count(applied_conn, "concepts", "project-x") == 4
    assert _count(applied_conn, "behaviors", "project-x") == 12


def test_seed_behavior_instruction_metadata(applied_conn: sqlite3.Connection) -> None:
    """1.2.3+: every seeded discipline rule must carry the right enum
    values and metadata so it's immediately visible in
    behavior section of the next memory_brief envelope.

    Each BI may pick its own (kind, priority, conflict_policy) — the
    capability-suggestion and search-before-write rules use operating_rule +
    user_preference + current_user_wins (overridable by the operator
    in-chat); the workflow rules (memory-first, no-push) tighten on
    purpose. All four MUST share source_type='seed_bootstrap' and
    active=True so they round-trip cleanly into the envelope."""
    seed_neutral_project_memory(applied_conn, workspace_id="project-x")
    rows = applied_conn.execute(
        "SELECT name, kind, scope, priority, conflict_policy, source_type, "
        "active, pinned, applies_to_json FROM behaviors "
        "WHERE workspace_id='project-x' ORDER BY name"
    ).fetchall()
    assert len(rows) == 12
    names = {r["name"] for r in rows}
    assert names == {
        "Record capability suggestion after every decision and theory write",
        "Search before write - auto-inject is not exhaustive",
        "Memory-first before reading or editing source",
        "No git commit/push/CI without explicit operator permission",
        "applies-to-checklist-must-be-stated-verbatim",
        "verification-claims-must-cite-prod-evidence",
        "memory-write-is-not-done-until-candidates-resolved",
        "pretooluse:no-magic-number-in-strategy",
        "pretooluse:decision-must-have-provenance",
        "pretooluse:read-before-edit",
        "pretooluse:impact-check-before-read",
        "pretooluse:search-before-architectural-write",
    }
    # Every seed BI must share the canonical seed-bootstrap source_type and
    # be active immediately so it shows up in the envelope.
    for row in rows:
        assert row["scope"] == "workspace", row["name"]
        assert row["source_type"] == "seed_bootstrap", row["name"]
        assert row["active"] in (1, True), row["name"]

    # Pinned subset (Phase 1.2 of v2.2 consolidation: memory-first + no-push;
    # v2.2.x 2026-05-13: applies-to-checklist + verification-cite + write-resolve).
    # All five pinned rules must ride every active envelope regardless of query.
    pinned_names = {r["name"] for r in rows if bool(r["pinned"])}
    assert pinned_names == {
        "Memory-first before reading or editing source",
        "No git commit/push/CI without explicit operator permission",
        "applies-to-checklist-must-be-stated-verbatim",
        "verification-claims-must-cite-prod-evidence",
        "memory-write-is-not-done-until-candidates-resolved",
        "pretooluse:no-magic-number-in-strategy",
        "pretooluse:decision-must-have-provenance",
        "pretooluse:read-before-edit",
        "pretooluse:impact-check-before-read",
        "pretooluse:search-before-architectural-write",
    }

    # The capability-suggestion rule applies_to research-mutating APIs
    cap_context = next(
        r
        for r in rows
        if r["name"] == "Record capability suggestion after every decision and theory write"
    )
    cap_applies = json.loads(cap_context["applies_to_json"] or "[]")
    assert "memory_write kind=decision" in cap_applies
    assert "memory_write kind=theory" in cap_applies
    assert "capability_suggestions field" in cap_applies
    assert cap_context["kind"] == "operating_rule"
    assert cap_context["priority"] == "user_preference"
    assert cap_context["conflict_policy"] == "current_user_wins"

    # The search-first rule applies_to writes that should be preceded by search
    search_rule = next(
        r for r in rows if r["name"] == "Search before write - auto-inject is not exhaustive"
    )
    search_applies = json.loads(search_rule["applies_to_json"] or "[]")
    assert "memory_write kind=decision" in search_applies
    assert "before architectural decisions" in search_applies
    assert search_rule["kind"] == "operating_rule"

    # The memory-first rule applies_to Read/Grep moments
    mem_first = next(
        r for r in rows if r["name"] == "Memory-first before reading or editing source"
    )
    mem_first_applies = json.loads(mem_first["applies_to_json"] or "[]")
    assert "before Read tool" in mem_first_applies
    assert "before Grep tool" in mem_first_applies
    assert mem_first["kind"] == "workflow_preference"

    # The no-push rule applies_to git operations
    no_push = next(
        r for r in rows if r["name"] == "No git commit/push/CI without explicit operator permission"
    )
    no_push_applies = json.loads(no_push["applies_to_json"] or "[]")
    assert "git push" in no_push_applies
    assert "shipping to main" in no_push_applies
    assert no_push["kind"] == "operating_rule"
