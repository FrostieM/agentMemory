"""Strip MCP function-call markup leaked into stored text fields.

Background: agents that called memory_* MCP tools while accidentally
including their own function-call markup (`</decision_text>`,
`<parameter name="...">`, `</invoke>`, etc.) inside parameter values
got that markup persisted verbatim. UI renders honestly, but the text
is garbage at the tail.

This script truncates each affected text field at the FIRST occurrence
of any strict marker, then trims trailing whitespace. The pre-truncation
prefix is the legitimate content; everything after is leaked
function-call boundary plus next-parameter content.

Idempotent: re-running on a clean DB is a no-op.

Usage:
    python scripts/repair_text_artifacts.py --db <path>           # dry run
    python scripts/repair_text_artifacts.py --db <path> --apply   # write
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Strict markers — only patterns that are unambiguous MCP function-call
# leakage. Operators may legitimately store XML/HTML/code, so we DO NOT
# touch generic angle brackets — only these specific closing tags and
# the parameter-block opener.
_MARKERS = (
    "</decision_text>",
    "</raw_text>",
    "</parameter>",
    "</invoke>",
    '<parameter name="',
)

# (table, column) pairs to scan + repair.
_TARGETS = (
    ("decisions", "decision_text"),
    ("decisions", "rationale"),
    ("episodes", "raw_text"),
    ("chunks", "text"),
    ("chunks", "summary"),
    ("insights", "summary"),
    ("insights", "proposed_action"),
    ("concepts", "definition"),
    ("skills", "summary"),
    ("skills", "body_md"),
    ("behaviors", "rule"),
    ("behaviors", "rationale"),
)


def _earliest_marker(text: str) -> int:
    """Return position of the first MCP marker in `text`, or -1 if none."""
    positions = [text.find(m) for m in _MARKERS]
    found = [p for p in positions if p >= 0]
    return min(found) if found else -1


def _clean(text: str | None) -> tuple[str | None, bool]:
    """Return (cleaned_text, was_changed). Truncates at first marker."""
    if text is None:
        return None, False
    pos = _earliest_marker(text)
    if pos < 0:
        return text, False
    truncated = text[:pos].rstrip()
    return truncated, truncated != text


def _extract_rationale(decision_text: str | None) -> str | None:
    """Pull the legitimate rationale content out of leaked
    `<parameter name="rationale">...</parameter>` block, if present.

    Many decisions wrote both `decision_text` and `rationale` into the
    decision_text column because the function-call markup leaked the
    parameter boundary. The structural rationale field stayed empty.
    Returns the inner text (stripped) or None if no rationale block found.
    """
    if not decision_text:
        return None
    marker = '<parameter name="rationale">'
    start = decision_text.find(marker)
    if start < 0:
        return None
    body_start = start + len(marker)
    end_markers = ("</parameter>", "</invoke>")
    end_positions = [decision_text.find(m, body_start) for m in end_markers]
    found = [p for p in end_positions if p >= 0]
    body_end = min(found) if found else len(decision_text)
    extracted = decision_text[body_start:body_end].strip()
    return extracted or None


def _repair_decisions_with_rationale(conn: sqlite3.Connection, *, apply: bool) -> tuple[int, int]:
    """Special-case decisions: try to recover rationale from the leaked
    `<parameter name="rationale">...</parameter>` block before truncating
    decision_text. Returns (text_repaired, rationale_recovered) counts.
    """
    rows = conn.execute(
        "SELECT id, decision_text, rationale FROM decisions WHERE decision_text IS NOT NULL"
    ).fetchall()
    text_fixed = 0
    rationale_recovered = 0
    for row in rows:
        cleaned_text, text_changed = _clean(row["decision_text"])
        if not text_changed:
            continue
        recovered = _extract_rationale(row["decision_text"])
        new_rationale: str | None = row["rationale"]
        # Only fill rationale if it was empty AND we have a recovered value.
        if recovered and not (row["rationale"] or "").strip():
            new_rationale = recovered
            rationale_recovered += 1
        text_fixed += 1
        if apply:
            conn.execute(
                "UPDATE decisions SET decision_text = ?, rationale = ? WHERE id = ?",
                (cleaned_text, new_rationale, row["id"]),
            )
    return text_fixed, rationale_recovered


def repair_db(db_path: Path, *, apply: bool) -> dict:
    """Scan + optionally repair every (table, column) target. Returns counts."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    summary: dict[str, int] = {}
    # Decisions get special handling: rationale extraction.
    text_fixed, rationale_recovered = _repair_decisions_with_rationale(conn, apply=apply)
    if text_fixed:
        summary["decisions.decision_text"] = text_fixed
    if rationale_recovered:
        summary["decisions.rationale (recovered from leaked text)"] = rationale_recovered
    for table, col in _TARGETS:
        # Skip decision_text — handled above with rationale extraction.
        # Plain pass for rationale (rows where rationale itself is contaminated
        # but decision_text was clean).
        if table == "decisions" and col == "decision_text":
            continue
        try:
            rows = conn.execute(
                f"SELECT id, {col} AS value FROM {table} WHERE {col} IS NOT NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        affected = 0
        for row in rows:
            cleaned, changed = _clean(row["value"])
            if not changed:
                continue
            affected += 1
            if apply:
                conn.execute(
                    f"UPDATE {table} SET {col} = ? WHERE id = ?",
                    (cleaned, row["id"]),
                )
        if affected:
            summary[f"{table}.{col}"] = summary.get(f"{table}.{col}", 0) + affected
    conn.close()
    return summary


def _print_summary(summary: dict, *, apply: bool) -> None:
    if not summary:
        print("  no contamination found — DB is clean")
        return
    label = "REPAIRED" if apply else "WOULD REPAIR"
    total = 0
    for key, count in sorted(summary.items()):
        print(f"  {label} {key}: {count} rows")
        total += count
    print(f"  total: {total} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite DB path")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"db not found: {db_path}")
    print(f"=== {db_path} ===")
    summary = repair_db(db_path, apply=args.apply)
    _print_summary(summary, apply=args.apply)


if __name__ == "__main__":
    main()
