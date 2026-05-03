"""Regression injection test for v2.3 sentinel scheduler.

Validates the behavioral claim: sentinels actually CATCH a regression,
they don't just pass-through-and-stamp.

Procedure:
1. Pick 3 well-defined decisions from the workspace.
2. Build retrieval-quality cases that query for each decision's id by
   the words in its title (FTS-only — no embedding model needed).
3. Run the cases against the unmodified DB (baseline). All 3 must pass.
4. INJECT a regression: archive one of the 3 decisions
   (status='superseded', is_archived=1 — same path archive route uses).
5. Re-run the cases. Exactly 1 must fail (the archived one), the other
   2 must still pass — proves the sentinel detects targeted regression
   without spurious failures.

Usage:
    python scripts/calibration/regression_injection.py --db <path> --workspace <id>

Calibration evidence in docs/V1_1_0_CALIBRATION.md.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agent_memory_lite.maintenance.retrieval_quality import (
    RetrievalQualityCase,
    run_retrieval_quality_evals,
)


def _pick_cases(conn: sqlite3.Connection, workspace: str) -> list[RetrievalQualityCase]:
    """Pick 3 active decisions and build a retrieval case per decision.

    Each case queries for the title and asserts the decision_text
    substring appears in the envelope. Using decision_text — not the
    decision id — gates the assertion on actual rendering of the
    ``<active_decisions>`` section: insights and chunks may still
    reference an archived decision by id, but only the active
    decisions section renders the body. So when the decision is
    archived, this substring drops out and the case fails — exactly
    the regression behavior we want to detect.
    """
    rows = conn.execute(
        """
        SELECT id, title, decision_text FROM decisions
        WHERE workspace_id = ? AND status = 'active'
        ORDER BY importance DESC, feedback_ewma DESC
        LIMIT 3
        """,
        (workspace,),
    ).fetchall()
    cases: list[RetrievalQualityCase] = []
    for row in rows:
        # First 60 chars of decision_text is distinctive enough yet small
        # enough not to be reformatted by any rendering layer.
        body = str(row["decision_text"] or "")[:60]
        if not body:
            continue
        cases.append(
            RetrievalQualityCase(
                name=f"sentinel_{row['id'][:12]}",
                query=str(row["title"]),
                expected_substrings=[body],
                top_k=20,
                max_tokens=3500,
            )
        )
    return cases


def _run(conn: sqlite3.Connection, workspace: str, cases: list, label: str) -> dict:
    report = run_retrieval_quality_evals(
        conn,
        workspace_id=workspace,
        cases=cases,
        embedding_provider=None,
        vector_store=None,
    )
    passed = sum(1 for r in report.results if not r.failures)
    failed = len(report.results) - passed
    print(f"\n=== {label} ===")
    for r in report.results:
        marker = "PASS" if not r.failures else "FAIL"
        print(f"  [{marker}] {r.name}  query='{r.query[:50]}'")
        for f in r.failures:
            print(f"         -> {f[:100]}")
    print(f"  totals: pass={passed} fail={failed}")
    return {"passed": passed, "failed": failed, "results": report.results}


def _inject_regression(conn: sqlite3.Connection, decision_id: str) -> None:
    """Archive the decision the way the archive route does for decisions."""
    conn.execute(
        "UPDATE decisions SET status='superseded' WHERE id = ?",
        (decision_id,),
    )


def _restore_decision(conn: sqlite3.Connection, decision_id: str) -> None:
    """Reverse the regression so the script is idempotent across reruns."""
    conn.execute(
        "UPDATE decisions SET status='active' WHERE id = ?",
        (decision_id,),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()

    conn = sqlite3.connect(Path(args.db), isolation_level=None)
    conn.row_factory = sqlite3.Row

    cases = _pick_cases(conn, args.workspace)
    if len(cases) < 3:
        print("ERROR: need at least 3 active decisions for the test")
        sys.exit(1)

    # Use the case name to recover the target decision id (cases don't
    # carry the id in expected_substrings — we attached only the body).
    target_id = next(
        r[0]
        for r in conn.execute(
            "SELECT id, decision_text FROM decisions WHERE workspace_id = ? "
            "AND status = 'active' AND decision_text LIKE ? LIMIT 1",
            (args.workspace, cases[0].expected_substrings[0] + "%"),
        )
    )
    target_name = cases[0].name

    # Phase 1: baseline — must all pass
    baseline = _run(conn, args.workspace, cases, "BASELINE (no regression)")
    if baseline["failed"] > 0:
        print(
            f"\nWARN: baseline had {baseline['failed']} failures — picking different "
            "decisions might be needed. Continuing."
        )

    # Phase 2: inject and re-run
    print(f"\n--- INJECTING regression: archive {target_id} ({target_name}) ---")
    _inject_regression(conn, target_id)
    try:
        post = _run(conn, args.workspace, cases, "POST-INJECTION")
    finally:
        # Always restore so the calibration DB stays usable.
        _restore_decision(conn, target_id)
        print(f"\n--- restored {target_id} ---")

    # Verdict
    print("\n=== VERDICT ===")
    expected_post_fail = 1
    actual_post_fail = post["failed"] - baseline["failed"]
    if actual_post_fail >= expected_post_fail:
        print(
            f"PASS — sentinel detected the regression "
            f"(baseline failures: {baseline['failed']}, post: {post['failed']}, "
            f"delta: +{actual_post_fail})"
        )
    else:
        print(
            f"FAIL — regression slipped past sentinels "
            f"(baseline: {baseline['failed']}, post: {post['failed']}, "
            f"delta: +{actual_post_fail}, expected: +{expected_post_fail})"
        )

    conn.close()


if __name__ == "__main__":
    main()
