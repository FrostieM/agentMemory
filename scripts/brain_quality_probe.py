"""Empirical quality probe -- not "did the code run" but "is the OUTPUT useful".

The crash test verifies behavior matches spec. This script asks the
harder question: when a real agent uses the brain, does it surface the
right rows, blocks the right tool calls, and produces narratives that
help rather than mislead?

Five probes, each printing concrete observations the operator can
eyeball:

  1. Recall on real topics -- does memory_recall return semantically
     related rows on actual copyBot concerns (kelly, calibrator,
     deploy, makerBot)?

  2. Hebbian accumulation under realistic search load -- fire 20+
     storage.reader.search calls that share themes; verify the graph
     captures the thematic cluster, not noise.

  3. Causal link semantic sanity -- sample 10 random invalidated /
     derived_from links and print the src/dst titles. Operator judges
     if the relations are real.

  4. Brief task-bias check -- does the brief surface the right
     decisions when the agent is about to work on a specific area
     (e.g. before-edit on src/maker_bot/calibrator/...)?

  5. Self-model coherence -- print the narrative verbatim. Operator
     judges if it sounds like "this workspace" or generic noise.

The script is read-mostly: it adds rows for the Hebbian probe (under
synthetic query hashes that are clearly marked) and one self-model
refresh. Run it on a SANDBOX copy of the DB, not live.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from agent_memory_lite.cognition.brief import compose_brief  # noqa: E402
from agent_memory_lite.cognition.self_model import (  # noqa: E402
    load_self_model,
    refresh_self_model,
)
from agent_memory_lite.maintenance.hebbian_pass import distill_workspace  # noqa: E402
from agent_memory_lite.retrieval.coactivation_log import log_coactivation  # noqa: E402
from agent_memory_lite.retrieval.recall import recall  # noqa: E402
from agent_memory_lite.storage.reader import search  # noqa: E402

GREEN = "\033[32m" if sys.stdout.isatty() else ""
YELLOW = "\033[33m" if sys.stdout.isatty() else ""
DIM = "\033[2m" if sys.stdout.isatty() else ""
RESET = "\033[0m" if sys.stdout.isatty() else ""


def hdr(text: str) -> None:
    print()
    print(f"{GREEN}{'=' * 70}{RESET}")
    print(f"{GREEN}{text}{RESET}")
    print(f"{GREEN}{'=' * 70}{RESET}")


def sub(text: str) -> None:
    print()
    print(f"{YELLOW}-- {text} --{RESET}")


def dim(text: str) -> None:
    print(f"{DIM}{text}{RESET}")


# ============================================================
# Probe 1 -- memory_recall on realistic topics
# ============================================================


def probe_recall(conn: sqlite3.Connection, ws: str) -> None:
    hdr("PROBE 1 -- memory_recall on real copyBot topics")
    topics = [
        "kelly",
        "calibrator",
        "makerBot",
        "deploy",
        "audit",
    ]
    for topic in topics:
        sub(f"recall(topic='{topic}', depth=2, outcome_floor=-1.0)")
        hits = recall(
            conn,
            workspace_id=ws,
            topic=topic,
            depth=2,
            outcome_floor=-1.0,
            limit=5,
        )
        if not hits:
            dim("  no hits (BM25 seed found nothing matching this substring)")
            continue
        for h in hits:
            title_or_gist = (
                h.projection.get("title")
                or h.projection.get("gist")
                or h.projection.get("name")
                or "?"
            )[:80]
            outcome = h.outcome_score
            causal = f" causal={len(h.causal_links)}" if h.causal_links else ""
            print(
                f"  [{h.kind:9s}] {h.object_id[:30]:30s}  "
                f"act={h.activation:.3f}  out={outcome:+.2f}{causal}  "
                f"{title_or_gist}"
            )


# ============================================================
# Probe 2 -- Hebbian accumulation under realistic search load
# ============================================================


def probe_hebbian(conn: sqlite3.Connection, ws: str) -> None:
    hdr("PROBE 2 -- Hebbian graph accumulation under simulated agent load")
    # Simulate a session where the agent investigates two distinct themes:
    # (A) calibrator volatility, (B) deploy / release management.
    # Each theme triggers 4 related searches. The Hebbian pass should
    # link items WITHIN a theme but NOT cross themes.
    sessions_theme_a = [
        "calibrator volatility tuning",
        "calibrator window threshold",
        "calibrator adaptive sampling",
        "kelly bidirectional drawdown",
    ]
    sessions_theme_b = [
        "deploy release production",
        "release pm2 health check",
        "deploy snapshot rollback",
        "production hot patch fix",
    ]
    sub("simulating session A (calibrator theme, 4 searches)")
    for query in sessions_theme_a:
        hits = search(
            conn,
            workspace_id=ws,
            query=query,
            limit=5,
            log_coactivations=False,
        )
        if hits:
            log_coactivation(
                conn,
                workspace_id=ws,
                query=query,
                hits=hits,
            )
            dim(f"  '{query[:40]}': {len(hits)} hits logged")
    sub("simulating session B (deploy theme, 4 searches)")
    for query in sessions_theme_b:
        hits = search(
            conn,
            workspace_id=ws,
            query=query,
            limit=5,
            log_coactivations=False,
        )
        if hits:
            log_coactivation(
                conn,
                workspace_id=ws,
                query=query,
                hits=hits,
            )
            dim(f"  '{query[:40]}': {len(hits)} hits logged")
    conn.commit()
    coact_count = conn.execute(
        "SELECT COUNT(*) FROM retrieval_coactivation WHERE workspace_id=?",
        (ws,),
    ).fetchone()[0]
    sub(f"coactivation log now has {coact_count} rows")
    sub("running hebbian_pass (HeLa-Mem gate ON)")
    edges_before_ws = conn.execute(
        "SELECT COUNT(*) FROM soft_edges WHERE workspace_id=? AND edge_kind='co_retrieved'",
        (ws,),
    ).fetchone()[0]
    up, gated = distill_workspace(
        conn,
        workspace_id=ws,
        outcome_gate=True,
    )
    conn.commit()
    edges_after_ws = conn.execute(
        "SELECT COUNT(*) FROM soft_edges WHERE workspace_id=? AND edge_kind='co_retrieved'",
        (ws,),
    ).fetchone()[0]
    dim(f"  edges before={edges_before_ws}  after={edges_after_ws}  upserted={up}  gated={gated}")
    if up == 0 and gated > 0:
        print(
            "  NOTE: every pair gated -- means all related rows had "
            "outcome_score=0. This is expected for a workspace where "
            "outcome_recompute hasn't propagated positive scores yet "
            "(decisions only get + scores from positive feedback)."
        )
    sub("top 10 co_retrieved soft edges in workspace (by weight)")
    rows = conn.execute(
        "SELECT src_qualified_name, dst_qualified_name, weight, "
        "observation_count FROM soft_edges WHERE workspace_id=? "
        "AND edge_kind='co_retrieved' ORDER BY weight DESC LIMIT 10",
        (ws,),
    ).fetchall()
    if not rows:
        dim("  (none yet)")
    for row in rows:
        print(f"  w={row[2]:.3f} obs={row[3]}  {row[0][:35]:35s} <-> {row[1][:35]:35s}")


# ============================================================
# Probe 3 -- causal link semantic sanity
# ============================================================


def probe_causal(conn: sqlite3.Connection, ws: str) -> None:
    hdr("PROBE 3 -- causal_links semantic sample")
    sub("10 random 'invalidated' links (src decision -> dst decision)")
    rows = conn.execute(
        "SELECT src_id, dst_id FROM causal_links "
        "WHERE workspace_id=? AND relation='invalidated' "
        "ORDER BY RANDOM() LIMIT 10",
        (ws,),
    ).fetchall()
    for src_id, dst_id in rows:
        src_title = _decision_title(conn, src_id)
        dst_title = _decision_title(conn, dst_id)
        print(f"  NEW: {src_title[:55]:55s}")
        print(f"   ->  OLD: {dst_title[:55]:55s}")
        print()
    sub("5 random 'derived_from' links (insight -> evidence episode)")
    rows = conn.execute(
        "SELECT src_id, dst_id FROM causal_links "
        "WHERE workspace_id=? AND relation='derived_from' "
        "ORDER BY RANDOM() LIMIT 5",
        (ws,),
    ).fetchall()
    for src_id, dst_id in rows:
        ins = conn.execute(
            "SELECT summary FROM insights WHERE id=?",
            (src_id,),
        ).fetchone()
        ep = conn.execute(
            "SELECT COALESCE(gist, raw_text) FROM episodes WHERE id=?",
            (dst_id,),
        ).fetchone()
        ins_text = (ins[0] if ins else "?")[:60]
        ep_text = (ep[0] if ep else "?")[:60]
        print(f"  INSIGHT: {ins_text}")
        print(f"    from EPISODE: {ep_text}")
        print()


def _decision_title(conn: sqlite3.Connection, dec_id: str) -> str:
    row = conn.execute(
        "SELECT title FROM decisions WHERE id=?",
        (dec_id,),
    ).fetchone()
    return row[0] if row else f"<missing {dec_id}>"


# ============================================================
# Probe 4 -- brief surface check on real workspace
# ============================================================


def probe_brief(conn: sqlite3.Connection, ws: str) -> None:
    hdr("PROBE 4 -- compose_brief on real copyBot workspace")
    from agent_memory_lite.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    brief = compose_brief(conn, workspace_id=ws, max_tokens=500)
    dim(f"brief body: {len(brief.body_md)} chars, {brief.token_count} tokens")
    dim(f"sections: {[s.name for s in brief.sections]}")
    print()
    print(brief.body_md)


# ============================================================
# Probe 5 -- self-model semantic coherence
# ============================================================


def probe_self_model(conn: sqlite3.Connection, ws: str) -> None:
    hdr("PROBE 5 -- self-model narrative (full text + invariants + uncertainties)")
    # Force a fresh refresh so the narrative reflects current row state.
    refresh_self_model(conn, workspace_id=ws)
    conn.commit()
    loaded = load_self_model(conn, workspace_id=ws)
    if loaded is None:
        print("  (no self_model row)")
        return
    dim(
        f"refreshed_via={loaded.refreshed_via}  "
        f"coverage={loaded.coverage_score:.2f}  "
        f"words={len(loaded.identity_text.split())}"
    )
    print()
    print(loaded.identity_text)
    print()
    sub(f"{len(loaded.invariants)} invariant decision ids (sample 5):")
    for did in loaded.invariants[:5]:
        print(f"  {_decision_title(conn, did)[:80]}")
    sub(f"{len(loaded.uncertainties)} uncertainty theory ids (sample 5):")
    for tid in loaded.uncertainties[:5]:
        row = conn.execute(
            "SELECT title FROM theories WHERE id=?",
            (tid,),
        ).fetchone()
        if row:
            print(f"  {row[0][:80]}")


# ============================================================
# Entrypoint
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--probe",
        default="all",
        choices=("all", "recall", "hebbian", "causal", "brief", "self_model"),
    )
    args = parser.parse_args()
    if not args.db.exists():
        print(f"db not found: {args.db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        probes: Iterable[tuple[str, object]] = (
            ("recall", probe_recall),
            ("hebbian", probe_hebbian),
            ("causal", probe_causal),
            ("brief", probe_brief),
            ("self_model", probe_self_model),
        )
        for name, fn in probes:
            if args.probe in ("all", name):
                fn(conn, args.workspace)  # type: ignore[operator]
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
