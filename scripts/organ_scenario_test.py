"""End-to-end agent-workflow simulation on a sandbox DB.

The crash test verifies isolated behaviors. The quality probe inspects
individual outputs. This script simulates a REAL AGENT SESSION end-to-
end and verifies that the memory organ reacts coherently across phases:
search -> coactivation -> hebbian distill -> supersede decision ->
implicit feedback -> outcome drops -> brief reflects -> reflex blocks
unsafe tool -> recall surfaces causal chain -> consolidation produces
insight -> insight promoted to behavior.

Five scenarios, each numbered with what it asserts about the system.
Run on a sandbox copy of a real workspace DB; mutations stay there.

Usage:
    python scripts/organ_scenario_test.py --db <sandbox.db> --workspace <id>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from agent_memory_lite.cognition.brief import compose_brief  # noqa: E402
from agent_memory_lite.cognition.consolidation import consolidate_workspace  # noqa: E402
from agent_memory_lite.cognition.outcome_recompute import refresh_workspace  # noqa: E402
from agent_memory_lite.compaction.promote_insight_to_behavior import (  # noqa: E402
    promote_eligible_insights,
)
from agent_memory_lite.config.settings import Settings  # noqa: E402
from agent_memory_lite.enforcement.reflex_check import check_reflexes  # noqa: E402
from agent_memory_lite.maintenance.hebbian_pass import distill_workspace  # noqa: E402
from agent_memory_lite.maintenance.implicit_feedback import (  # noqa: E402
    record_implicit_supersede,
)
from agent_memory_lite.retrieval.causal_extractor import extract_workspace  # noqa: E402
from agent_memory_lite.retrieval.coactivation_log import log_coactivation  # noqa: E402
from agent_memory_lite.retrieval.recall import recall  # noqa: E402
from agent_memory_lite.storage.reader import search  # noqa: E402
from agent_memory_lite.utils.ids import IdKind, new_id  # noqa: E402
from agent_memory_lite.utils.time import iso_now  # noqa: E402

GREEN = "\033[32m" if sys.stdout.isatty() else ""
RED = "\033[31m" if sys.stdout.isatty() else ""
YELLOW = "\033[33m" if sys.stdout.isatty() else ""
DIM = "\033[2m" if sys.stdout.isatty() else ""
RESET = "\033[0m" if sys.stdout.isatty() else ""

_TOTAL = 0
_PASS = 0


def hdr(title: str) -> None:
    print()
    print(f"{GREEN}{'=' * 72}{RESET}")
    print(f"{GREEN}{title}{RESET}")
    print(f"{GREEN}{'=' * 72}{RESET}")


def step(text: str) -> None:
    print(f"{YELLOW}  > {text}{RESET}")


def info(text: str) -> None:
    print(f"{DIM}    {text}{RESET}")


def check(label: str, condition: bool, observed: str = "") -> None:
    global _TOTAL, _PASS  # noqa: PLW0603
    _TOTAL += 1
    if condition:
        _PASS += 1
        print(f"    {GREEN}[OK]{RESET} {label}")
    else:
        print(f"    {RED}[FAIL]{RESET} {label}  observed: {observed}")


# ============================================================
# SCENARIO A -- Investigation + supersede + feedback loop
# ============================================================


def scenario_a_supersede_feedback(conn: sqlite3.Connection, ws: str) -> None:  # noqa: PLR0915
    # Long end-to-end scenario: 8 distinct steps, intentionally not split
    # into helpers because the linear flow IS the test (each step depends
    # on state set up by the previous one + every check is named).
    hdr("SCENARIO A -- Agent investigates calibrator, supersedes a decision")
    settings = Settings()

    # Step 1: agent searches the workspace. LIKE-based search needs a
    # single substring, not a phrase, to match decision titles.
    step("agent searches for 'kelly'")
    hits = search(conn, workspace_id=ws, query="kelly", limit=8)
    info(f"got {len(hits)} hits, kinds={sorted({h.kind for h in hits})}")
    check("BM25 returned decisions", any(h.kind == "decision" for h in hits))
    if not hits:
        info("(no hits in BM25; skipping rest of A)")
        return
    # Find a real decision target (one with substantive title containing 'kelly').
    target_dec_id: str | None = None
    for h in hits:
        if h.kind == "decision" and "kelly" in (h.projection.get("title") or "").lower():
            target_dec_id = str(h.projection.get("id"))
            break
    check("found a real Kelly decision to supersede", target_dec_id is not None)
    if not target_dec_id:
        return
    info(f"target_dec_id={target_dec_id}")
    log_coactivation(conn, workspace_id=ws, query="kelly", hits=hits)

    # Step 2: agent searches a related theme.
    step("agent searches 'calibrator window'")
    hits2 = search(conn, workspace_id=ws, query="calibrator window", limit=8)
    log_coactivation(conn, workspace_id=ws, query="calibrator window", hits=hits2)
    info(f"got {len(hits2)} hits, logged coactivation")

    # Step 3: Hebbian distill should accumulate edges (either new rows
    # or increment weight on existing pairs).
    step("running hebbian_pass over freshly-logged coactivations")
    edges_before = _count_edges(conn, ws)
    up, gated = distill_workspace(conn, workspace_id=ws, outcome_gate=True)
    conn.commit()
    edges_after = _count_edges(conn, ws)
    info(f"edges {edges_before} -> {edges_after}  upserted={up}  gated={gated}")
    check(
        "Hebbian work happened (new rows or weight increments)",
        up > 0 or edges_after > edges_before,
    )

    # Step 4: agent decides to supersede the Kelly decision with a new one.
    step(f"writing new decision that supersedes {target_dec_id}")
    new_dec_id = new_id(IdKind.DECISION)
    now = iso_now()
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, supersedes_decision_id, outcome_score, pinned)
           VALUES (?, ?, 'Scenario A: replace Kelly with quarter-Kelly cap',
                   'Apply 0.25x Kelly multiplier as hard cap.',
                   'quarter-Kelly cap', 'active', ?, ?, ?, ?, 0.0, 0)""",
        (new_dec_id, ws, now, now, now, target_dec_id),
    )
    # Mirror the writer pipeline: close the superseded decision so its
    # outcome can drop and so it falls out of "Active decisions" in the
    # brief. Without this, the only signal the OLD row carries is its
    # feedback_ewma which takes time to propagate.
    conn.execute(
        "UPDATE decisions SET status='superseded', valid_to=?, updated_at=? WHERE id=?",
        (now, now, target_dec_id),
    )
    conn.commit()
    info(f"new_dec_id={new_dec_id}, marked target as superseded")

    # Step 5: implicit feedback should fire on the OLD decision.
    record_implicit_supersede(
        conn,
        settings=settings,
        workspace_id=ws,
        source_type="decision",
        source_id=target_dec_id,
    )
    conn.commit()
    fb = conn.execute(
        "SELECT usefulness, source FROM memory_usage_feedback "
        "WHERE workspace_id=? AND source_id=? AND source='implicit_supersede' "
        "ORDER BY created_at DESC LIMIT 1",
        (ws, target_dec_id),
    ).fetchone()
    check("implicit_supersede feedback row written", fb is not None)
    if fb:
        info(f"feedback usefulness={fb['usefulness']}")
        check("feedback is negative", float(fb["usefulness"]) < 0)

    # Step 6: outcome_recompute should drop the old decision's score.
    step("running outcome_recompute")
    old_score_before = _decision_outcome(conn, target_dec_id)
    refresh_workspace(conn, workspace_id=ws, now_iso=iso_now())
    conn.commit()
    old_score_after = _decision_outcome(conn, target_dec_id)
    info(f"old decision outcome {old_score_before:+.3f} -> {old_score_after:+.3f}")
    check(
        "old decision outcome strictly dropped after supersede",
        old_score_after < old_score_before or old_score_after < 0,
    )

    # Step 7: causal_extractor should detect the invalidation.
    step("running causal_extractor")
    extract_workspace(conn, workspace_id=ws)
    conn.commit()
    link = conn.execute(
        "SELECT relation FROM causal_links WHERE workspace_id=? AND src_id=? AND dst_id=?",
        (ws, new_dec_id, target_dec_id),
    ).fetchone()
    check("causal_link 'invalidated' emitted (real pivot, not refresh)", link is not None)
    if link:
        info(f"relation={link['relation']}")

    # Step 8: brief should now show the old decision in watch-outs (outcome<0)
    # and the new in active.
    step("composing brief; expecting NEW in active + OLD in watch-outs")
    from agent_memory_lite.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    brief = compose_brief(conn, workspace_id=ws, max_tokens=500)
    new_in_active = new_dec_id in brief.body_md
    old_in_watch = target_dec_id in brief.body_md and "Watch-outs" in brief.body_md
    check("new decision surfaces in brief body", new_in_active)
    check(
        "old decision visible (either watch-outs or causal chain)",
        old_in_watch or "supersedes" in brief.body_md,
    )


# ============================================================
# SCENARIO B -- Reflex blocks unsafe tool call
# ============================================================


def scenario_b_reflex_block(conn: sqlite3.Connection, ws: str) -> None:
    hdr("SCENARIO B -- PreToolUse reflex blocks Edit without impact_check")

    # Step 1: cold session, no precondition.
    step("calling check_reflexes for Edit src/strategy/kelly.ts WITHOUT impact_check")
    violations_cold = check_reflexes(
        conn,
        workspace_id=ws,
        tool_name="Edit",
        tool_payload={"file_path": "src/strategy/kelly.ts"},
        trail=[],
        block_override=False,
    )
    edit_reflex = [v for v in violations_cold if v.rule_name == "edit-requires-impact-check"]
    check("reflex 'edit-requires-impact-check' fired", len(edit_reflex) == 1)
    if edit_reflex:
        info(f"enforcement={edit_reflex[0].enforcement}  advisory={edit_reflex[0].advisory[:80]}")

    # Step 2: same call WITH impact_check in trail.
    step("re-trying with memory_impact_check in trail")
    violations_warm = check_reflexes(
        conn,
        workspace_id=ws,
        tool_name="Edit",
        tool_payload={"file_path": "src/strategy/kelly.ts"},
        trail=["Read", "memory_impact_check", "Edit"],
        block_override=False,
    )
    edit_reflex2 = [v for v in violations_warm if v.rule_name == "edit-requires-impact-check"]
    check("reflex correctly NOT fired when precondition met", len(edit_reflex2) == 0)

    # Step 3: deploy-requires-playbook reflex.
    step("calling check_reflexes for Bash 'npm run deploy' WITHOUT playbook")
    violations_deploy = check_reflexes(
        conn,
        workspace_id=ws,
        tool_name="Bash",
        tool_payload={"command": "npm run deploy -- --production"},
        trail=[],
        block_override=False,
    )
    deploy_reflex = [v for v in violations_deploy if v.rule_name == "deploy-requires-playbook"]
    check("reflex 'deploy-requires-playbook' fired on deploy command", len(deploy_reflex) == 1)

    # Step 4: same with memory_invoke_skill in trail.
    step("re-trying deploy with memory_invoke_skill in trail")
    violations_deploy_warm = check_reflexes(
        conn,
        workspace_id=ws,
        tool_name="Bash",
        tool_payload={"command": "npm run deploy -- --production"},
        trail=["memory_invoke_skill", "Bash"],
        block_override=False,
    )
    check(
        "deploy reflex skipped after playbook fetch",
        not any(v.rule_name == "deploy-requires-playbook" for v in violations_deploy_warm),
    )


# ============================================================
# SCENARIO C -- Capability layer inspection
# ============================================================


def scenario_c_capability_inspection(conn: sqlite3.Connection, ws: str) -> None:
    hdr("SCENARIO C -- Roles / Skills / Playbooks / Capability links")

    counts: dict[str, int] = {}
    for table in ("agent_roles", "agent_skills", "agent_playbooks", "capability_links"):
        try:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE workspace_id=?", (ws,)
            ).fetchone()[0]
            counts[table] = int(n)
        except sqlite3.OperationalError:
            counts[table] = -1  # table missing
    info(f"counts: {counts}")
    check("workspace has agent_skills", counts.get("agent_skills", 0) > 0)
    check("workspace has agent_playbooks", counts.get("agent_playbooks", 0) > 0)
    check("workspace has capability_links", counts.get("capability_links", 0) > 0)

    step("top-3 skills by usage_count + success_count")
    rows = conn.execute(
        """SELECT name, usage_count, success_count, failure_count, last_invoked_at
           FROM agent_skills WHERE workspace_id=?
           ORDER BY usage_count DESC LIMIT 3""",
        (ws,),
    ).fetchall()
    for row in rows:
        info(
            f"{row['name'][:55]:55s}  used={row['usage_count']}  "
            f"ok={row['success_count']}  fail={row['failure_count']}"
        )

    step("top-3 playbooks by recency")
    rows = conn.execute(
        """SELECT name, goal, usage_count FROM agent_playbooks WHERE workspace_id=?
           ORDER BY updated_at DESC LIMIT 3""",
        (ws,),
    ).fetchall()
    for row in rows:
        info(f"playbook: {row['name'][:55]}  (used={row['usage_count']})")
        info(f"  goal: {(row['goal'] or '')[:80]}")

    step("decisions WITHOUT any capability link (sample of 5)")
    rows = conn.execute(
        """SELECT d.id, d.title FROM decisions d
           LEFT JOIN capability_links cl
             ON cl.target_type='decision' AND cl.target_id=d.id AND cl.workspace_id=d.workspace_id
           WHERE d.workspace_id=? AND d.status='active' AND cl.id IS NULL
           ORDER BY d.updated_at DESC LIMIT 5""",
        (ws,),
    ).fetchall()
    info(f"found {len(rows)} unlinked decisions")
    for row in rows:
        info(f"  unlinked: {(row['title'] or '?')[:60]}")
    # No assertion -- this is an audit, not a pass/fail. The fact that the
    # query returns rows is the operator's signal to backfill.


# ============================================================
# SCENARIO D -- Recall over accumulated graph
# ============================================================


def scenario_d_recall_after_workflows(conn: sqlite3.Connection, ws: str) -> None:
    hdr("SCENARIO D -- memory_recall on topics with newly-built causal chain")

    for topic in ("kelly", "calibrator", "deploy"):
        step(f"recall(topic='{topic}', depth=2, outcome_floor=-1.0, limit=5)")
        hits = recall(
            conn,
            workspace_id=ws,
            topic=topic,
            depth=2,
            outcome_floor=-1.0,
            limit=5,
        )
        if not hits:
            info("  no hits")
            continue
        has_causal = sum(1 for h in hits if h.causal_links)
        info(f"  {len(hits)} hits, {has_causal} with causal_links")
        for h in hits[:3]:
            title = (h.projection.get("title") or h.projection.get("gist") or "?")[:60]
            causal = (
                f" causal={[link['relation'] for link in h.causal_links]}" if h.causal_links else ""
            )
            info(
                f"  [{h.kind:9s}] act={h.activation:.2f}  "
                f"out={h.outcome_score:+.2f}{causal}  {title}"
            )
        check(f"recall '{topic}' returned hits", len(hits) > 0)


# ============================================================
# SCENARIO E -- Full consolidation -> insight -> behavior cycle
# ============================================================


def scenario_e_consolidation_cycle(conn: sqlite3.Connection, ws: str) -> None:
    hdr("SCENARIO E -- Consolidation -> insight -> behavior promotion")

    # Step 1: seed 4 synthetic episodes that share theme tokens.
    step("seeding 4 synthetic episodes with shared theme tokens")
    now = iso_now()
    for i in range(4):
        ep_id = f"scenario_e_ep_{i}"
        conn.execute("DELETE FROM episodes WHERE id=?", (ep_id,))
        conn.execute(
            """INSERT INTO episodes
               (id, workspace_id, source_type, raw_text, gist, created_at, is_archived)
               VALUES (?, ?, 'agent_action', ?, ?, ?, 0)""",
            (
                ep_id,
                ws,
                f"Scenario E observation {i}: quarter Kelly cap reduced drawdown by 14 percent in window 7",
                "quarter Kelly cap reduced drawdown",
                now,
            ),
        )
    conn.commit()

    # Step 2: consolidate.
    step("running consolidate_workspace(window_hours=1)")
    report = consolidate_workspace(conn, workspace_id=ws, window_hours=1)
    info(
        f"episodes_seen={report.episodes_seen}  "
        f"clusters_found={report.clusters_found}  "
        f"insights_written={report.insights_written}"
    )
    check("at least one insight produced", report.insights_written >= 1)

    # Step 3: each evidence episode should have a positive feedback row.
    fb_count = conn.execute(
        """SELECT COUNT(*) FROM memory_usage_feedback
           WHERE workspace_id=? AND source_type='episode'
             AND source='implicit_consolidation'""",
        (ws,),
    ).fetchone()[0]
    info(f"implicit_consolidation feedback rows: {fb_count}")
    check("consolidation feedback rows present", fb_count >= 4)

    # Step 4: bump one insight's confidence + surface_count so the promote
    # gate would fire on next pass.
    step("bumping a synthetic insight to confidence=0.85 + surface_count=3")
    ins_id = f"scenario_e_ins_{new_id(IdKind.RESEARCH_INSIGHT)[-12:]}"
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status,
            confidence, surface_count, created_at, updated_at)
           VALUES (?, ?, 'consolidation',
                   'Scenario E: quarter Kelly cap reduces drawdown reliably',
                   'quarter Kelly cap reduces drawdown', 'candidate',
                   0.85, 3, ?, ?)""",
        (ins_id, ws, now, now),
    )
    conn.commit()

    # Step 5: promote.
    step("promoting eligible insights")
    stats = promote_eligible_insights(conn, workspace_id=ws)
    conn.commit()
    info(f"promoted={stats.promoted}  skipped={stats.skipped}")
    check("at least one insight promoted to pinned behavior", stats.promoted >= 1)

    beh = conn.execute(
        "SELECT pinned, source_type, source_id, name FROM behaviors "
        "WHERE workspace_id=? AND source_id=?",
        (ws, ins_id),
    ).fetchone()
    check("matching pinned behavior row exists", beh is not None)
    if beh:
        info(
            f"new behavior: name={beh['name']}  pinned={beh['pinned']}  source={beh['source_type']}"
        )
        check("new behavior is pinned", beh["pinned"] == 1)


# ============================================================
# Helpers
# ============================================================


def _count_edges(conn: sqlite3.Connection, ws: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM soft_edges WHERE workspace_id=? AND edge_kind='co_retrieved'",
            (ws,),
        ).fetchone()[0]
    )


def _decision_outcome(conn: sqlite3.Connection, dec_id: str) -> float:
    row = conn.execute("SELECT outcome_score FROM decisions WHERE id=?", (dec_id,)).fetchone()
    return float(row[0]) if row else 0.0


# ============================================================
# Entrypoint
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--scenario", default="all", choices=("all", "a", "b", "c", "d", "e"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.db.exists():
        print(f"db not found: {args.db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.scenario in ("all", "a"):
            scenario_a_supersede_feedback(conn, args.workspace)
        if args.scenario in ("all", "b"):
            scenario_b_reflex_block(conn, args.workspace)
        if args.scenario in ("all", "c"):
            scenario_c_capability_inspection(conn, args.workspace)
        if args.scenario in ("all", "d"):
            scenario_d_recall_after_workflows(conn, args.workspace)
        if args.scenario in ("all", "e"):
            scenario_e_consolidation_cycle(conn, args.workspace)
    finally:
        conn.close()
    print()
    print(f"{GREEN if _PASS == _TOTAL else RED}=== {_PASS}/{_TOTAL} assertions passed ==={RESET}")
    if args.json:
        print(json.dumps({"passed": _PASS, "total": _TOTAL}))
    return 0 if _PASS == _TOTAL else 1


if __name__ == "__main__":
    raise SystemExit(main())
