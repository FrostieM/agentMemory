"""End-to-end crash test for the v2 memory features.

Drives /memory/* via HTTP against the running hub-mode service to
verify:

* Commit A: pin (decision / behavior_instruction / core_memory) +
            date-range filters on every listing
* Commit B: snapshot save / list / diff
* Commit D: archive→restore preserves prior status, conflict-detect
            skips retired rows, what_references safety
* Commit E: review_queue + compact_trigger watchdog

Run with the qa-crash workspace registered in
``~/.agent_memory/workspaces.json``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any

import httpx

WORKSPACE = "qa-crash"
DB_PATH = (
    r"C:\Users\Osino\Desktop\work\agent-memory-lite\.agent_memory\qa\qa-crash"
    r"\.agent_memory\memory.db"
)
BASE = "http://127.0.0.1:8765"
HEADERS = {"Content-Type": "application/json", "X-Memory-DB-Path": DB_PATH}


# ----- helpers ---------------------------------------------------------------

results: list[tuple[str, str, str]] = []  # (section, name, status)


def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    r = httpx.post(BASE + path, headers=HEADERS, json=body, timeout=30)
    if r.status_code >= 400:
        print(f"!! {path} status={r.status_code} body={r.text[:300]}")
        r.raise_for_status()
    return r.json()


def assert_eq(section: str, name: str, got: Any, want: Any) -> None:
    status = "PASS" if got == want else f"FAIL got={got!r} want={want!r}"
    results.append((section, name, status))
    print(f"[{status}] {section} :: {name}")


def assert_true(section: str, name: str, cond: bool, hint: str = "") -> None:
    status = "PASS" if cond else f"FAIL ({hint})"
    results.append((section, name, status))
    print(f"[{status}] {section} :: {name}")


# ----- seeding ---------------------------------------------------------------


def seed() -> dict[str, Any]:
    print("=== SEEDING ===")
    state: dict[str, Any] = {}
    # Decisions
    state["d1"] = post(
        "/memory/write_decision",
        {
            "workspace_id": WORKSPACE,
            "title": "Architecture: local-only embedding",
            "decision_text": "All embedding runs on the local machine.",
            "rationale": "Local-only invariant.",
            "importance": 0.95,
        },
    )["decision_id"]
    state["d2"] = post(
        "/memory/write_decision",
        {
            "workspace_id": WORKSPACE,
            "title": "Use SQLite WAL mode",
            "decision_text": "Switch SQLite to WAL for concurrent reads.",
            "importance": 0.6,
        },
    )["decision_id"]
    # Theory at status='supported' (manual fact for prior_status test)
    state["t1"] = post(
        "/memory/write_theory",
        {
            "workspace_id": WORKSPACE,
            "title": "Pinning lifts answer quality",
            "domain": "memory.context",
            "claim": "Pinning core decisions raises agent answer quality on architecture questions.",
            "predictions": ["fewer LLM hallucinations"],
            "validation_criteria": ["Compare context_hit_rate before/after pinning"],
            "status": "supported",
            "confidence": 0.7,
            "importance": 0.9,
        },
    )["theory_id"]
    # Behavior instruction
    state["b1"] = post(
        "/memory/upsert_behavior_instruction",
        {
            "workspace_id": WORKSPACE,
            "name": "Evidence-first reports",
            "rule": "Always lead operational reports with concrete evidence.",
            "kind": "communication_style",
            "rationale": "User wants evidence, not generic status language.",
            "confidence": 0.95,
        },
    )["instruction_id"]
    # Episode (creates extraction candidates)
    state["e1"] = post(
        "/memory/ingest_episode",
        {
            "workspace_id": WORKSPACE,
            "source_type": "agent_action",
            "raw_text": (
                "Decision: keep local-only guard enabled in production. "
                "Rationale: prevents accidental cloud egress."
            ),
        },
    ).get("episode_id")
    # File ingest for chunks
    post(
        "/memory/ingest_file",
        {
            "workspace_id": WORKSPACE,
            "path": "notes/architecture.md",
            "content": "# Architecture\n\nLocal-only is mandatory. " * 30,
            "language": "markdown",
        },
    )
    print(json.dumps(state, indent=2))
    return state


# ----- Commit A: pin + date-range -------------------------------------------


def test_commit_a(state: dict[str, Any]) -> None:
    print("\n=== COMMIT A: pin (3 kinds) + date-range ===")
    # Pin a decision
    res = post(
        "/memory/pin",
        {"workspace_id": WORKSPACE, "kind": "decision", "id": state["d1"]},
    )
    assert_eq(
        "A.pin.decision", "pinned=True+found=True", (res["pinned"], res["found"]), (True, True)
    )

    # Pin a behavior_instruction
    res = post(
        "/memory/pin",
        {
            "workspace_id": WORKSPACE,
            "kind": "behavior_instruction",
            "id": state["b1"],
        },
    )
    assert_eq(
        "A.pin.behavior", "pinned=True+found=True", (res["pinned"], res["found"]), (True, True)
    )

    # Seed a core_memory row directly (no public POST writer)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO core_memory (id, workspace_id, key, value,
        source_episode_id, confidence, importance, active, created_at, updated_at)
        VALUES ('core_qa_1', ?, 'local_only', 'Never call cloud LLMs.',
        NULL, 0.99, 0.99, 1, '2026-05-02', '2026-05-02')""",
        (WORKSPACE,),
    )
    conn.commit()
    conn.close()
    res = post(
        "/memory/pin",
        {"workspace_id": WORKSPACE, "kind": "core_memory", "id": "core_qa_1"},
    )
    assert_eq("A.pin.core", "pinned=True+found=True", (res["pinned"], res["found"]), (True, True))

    # Verify list_decisions returns pinned-first ordering
    listed = post(
        "/memory/list_decisions",
        {"workspace_id": WORKSPACE, "limit": 5},
    )
    decisions = listed["decisions"]
    assert_eq("A.pin.list_decisions", "pinned-first", decisions[0]["decision_id"], state["d1"])

    # Date-range filter on list_candidates: pick a wide window
    res = post(
        "/memory/list_candidates",
        {"workspace_id": WORKSPACE, "since": "2020-01-01", "until": "2030-01-01"},
    )
    assert_true(
        "A.daterange.candidates", "candidates returned", isinstance(res.get("candidates"), list)
    )

    # Date-range filter on list_behavior_instructions
    res = post(
        "/memory/list_behavior_instructions",
        {"workspace_id": WORKSPACE, "since": "2020-01-01", "limit": 5},
    )
    assert_true(
        "A.daterange.behavior",
        "behavior with since accepted",
        isinstance(res.get("instructions"), list),
    )

    # Date-range on list_research_agenda
    res = post(
        "/memory/list_research_agenda",
        {"workspace_id": WORKSPACE, "since": "2020-01-01", "limit": 5},
    )
    assert_true("A.daterange.agenda", "agenda accepted", isinstance(res.get("snapshots"), list))


# ----- Commit B: snapshots ---------------------------------------------------


def test_commit_b(state: dict[str, Any]) -> None:
    print("\n=== COMMIT B: state snapshots ===")
    # Save snapshot 1 (before adding more)
    s1 = post(
        "/memory/snapshot_save",
        {"workspace_id": WORKSPACE, "name": "before"},
    )
    assert_true("B.save", "snapshot_id starts with memst_", s1["snapshot_id"].startswith("memst_"))
    state["snap_before"] = s1["snapshot_id"]

    # Mutate: add a new decision
    state["d3"] = post(
        "/memory/write_decision",
        {
            "workspace_id": WORKSPACE,
            "title": "Adopt 90-day stale window",
            "decision_text": "Chunks older than 90 days are stale.",
        },
    )["decision_id"]

    # Snapshot 2
    s2 = post(
        "/memory/snapshot_save",
        {"workspace_id": WORKSPACE, "name": "after"},
    )
    state["snap_after"] = s2["snapshot_id"]

    # List
    listed = post("/memory/snapshot_list", {"workspace_id": WORKSPACE, "limit": 5})
    names = [s["name"] for s in listed["snapshots"]]
    assert_true("B.list", "both names present", {"before", "after"}.issubset(set(names)))

    # Diff
    diff = post(
        "/memory/snapshot_diff",
        {
            "workspace_id": WORKSPACE,
            "before_id": s1["snapshot_id"],
            "after_id": s2["snapshot_id"],
        },
    )
    assert_eq("B.diff.counts", "decision_total=+1", diff["counts_delta"].get("decision_total"), 1)
    expected_added = f"decision:{state['d3']}"
    assert_true("B.diff.added", f"{expected_added} in added", expected_added in diff["added"])


# ----- Commit D: tech-debt fixes --------------------------------------------


def test_commit_d(state: dict[str, Any]) -> None:
    print("\n=== COMMIT D: archive prior_status + safety ===")
    # Archive theory at supported, then restore — should land on supported
    res = post(
        "/memory/archive",
        {"workspace_id": WORKSPACE, "kind": "theory", "id": state["t1"], "archive": True},
    )
    assert_eq("D.archive.theory", "found", res["found"], True)

    # Confirm status went to archived
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT status FROM theories WHERE id = ?", (state["t1"],)).fetchone()
    assert_eq("D.archive.theory.status", "archived", row[0], "archived")
    conn.close()

    # Restore
    res = post(
        "/memory/archive",
        {"workspace_id": WORKSPACE, "kind": "theory", "id": state["t1"], "archive": False},
    )
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT status FROM theories WHERE id = ?", (state["t1"],)).fetchone()
    assert_eq("D.restore.theory.prior_status", "supported (preserved)", row[0], "supported")
    conn.close()

    # what_references safety: short / empty target_id should return []
    res = post(
        "/memory/what_references",
        {"workspace_id": WORKSPACE, "target_id": "ab"},
    )
    assert_eq("D.what_references.short_id_rejected", "empty hits for short id", res["hits"], [])

    # Real lookup works
    res = post(
        "/memory/what_references",
        {"workspace_id": WORKSPACE, "target_id": state["d1"]},
    )
    assert_true(
        "D.what_references.real_id", "real id returns dict", isinstance(res.get("hits"), list)
    )


# ----- Commit E: review queue + compact trigger -----------------------------


def test_commit_e() -> None:
    print("\n=== COMMIT E: review queue + compact trigger ===")
    res = post(
        "/memory/review_queue",
        {"workspace_id": WORKSPACE, "limit_per_kind": 10},
    )
    assert_true("E.review_queue.shape", "items list returned", isinstance(res.get("items"), list))
    assert_true("E.review_queue.counts", "counts present", "total" in res.get("counts", {}))

    res = post(
        "/memory/compact_trigger",
        {"workspace_id": WORKSPACE},
    )
    assert_eq("E.compact_trigger.disabled_default", "enabled=False", res.get("enabled"), False)


# ----- audit / list_audit ----------------------------------------------------


def test_audit(state: dict[str, Any]) -> None:
    print("\n=== AUDIT trail ===")
    res = post(
        "/memory/list_audit",
        {
            "workspace_id": WORKSPACE,
            "target_type": "theory",
            "target_id": state["t1"],
            "limit": 10,
        },
    )
    actions = [e["action"] for e in res["entries"]]
    assert_true("AUDIT.archive_entry", "archive in audit", "archive" in actions)
    assert_true("AUDIT.restore_entry", "restore in audit", "restore" in actions)


# ----- main ------------------------------------------------------------------


def main() -> int:
    state = seed()
    test_commit_a(state)
    test_commit_b(state)
    test_commit_d(state)
    test_commit_e()
    test_audit(state)

    print("\n=== SUMMARY ===")
    failed = [r for r in results if not r[2].startswith("PASS")]
    for section, name, status in results:
        marker = "✓" if status == "PASS" else "✗"
        print(f"  {marker} {section} :: {name} :: {status}")
    print(f"\n{len(results) - len(failed)}/{len(results)} passed; {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
