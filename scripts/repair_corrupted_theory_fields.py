"""Repair theories whose tool-form XML escape leaked into ``claim``.

Symptom (seen in copyBot, 2026-05-02):

    SELECT title, claim, mechanism, predictions
    FROM theories WHERE id = 'th_5ddd3157d595db85';
    -- claim contains:
    --   "...real claim text...</claim>
    --    <parameter name=\"mechanism\">...real mechanism text...</parameter>
    --    <parameter name=\"predictions\">[...JSON list...]</parameter>"
    -- mechanism is NULL, predictions is []. The fields the agent meant
    -- to set ended up baked into the claim string.

Root cause: when an agent wrote the theory through ``memory_write_theory``
it produced Claude tool-form XML (``<parameter name=...>``) inside what
should have been the plain-string ``claim`` field. The MCP server stored
exactly what the agent sent. The repair is purely data-level — it moves
embedded ``<parameter name="X">VAL</parameter>`` fragments into the
matching DB columns when those columns are still empty, and strips the
escape garbage out of ``claim`` / ``experiment_plan``.

Usage::

    python scripts/repair_corrupted_theory_fields.py --workspace copyBot \\
        --db-path C:/.../memory.db --json                # dry-run report
    python scripts/repair_corrupted_theory_fields.py --workspace copyBot \\
        --db-path C:/.../memory.db --apply --backup-first

The script never overwrites a non-empty column. It also skips theories
whose ``claim`` does not contain the tell-tale ``</`` ``parameter`` text,
so it is safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Embedded fields the broken tool-form XML can leak into claim. The keys
# are the <parameter name="..."> values; the value is the column the
# extracted text must land in (or None when we just strip the segment).
_EMBEDDED_FIELDS: dict[str, str | None] = {
    "mechanism": "mechanism",
    "predictions": "predictions_json",
    "validation_criteria": "validation_criteria_json",
    "experiment_plan": "experiment_plan",
    "tags": "tags_json",
    "dependent_decision_ids": "dependent_decision_ids_json",
    "rationale": None,  # decisions only — keep recipe symmetric
}

_LIST_COLUMNS = {
    "predictions_json",
    "validation_criteria_json",
    "tags_json",
    "dependent_decision_ids_json",
}

_PARAM_OPEN_RE = re.compile(r"<parameter\s+name=\"([^\"]+)\">", flags=re.DOTALL)
_PARAM_CLOSE_RE = re.compile(r"</parameter>", flags=re.DOTALL)
# Stray closing tags the broken serializer can drop in front of the
# first <parameter ...> block: </claim>, </decision_text>, and a
# spurious </parameter> when the previous block was the original field.
_TRAILING_TAG_RE = re.compile(
    r"(</claim>|</decision_text>|</parameter>)\s*",
    flags=re.DOTALL,
)


def _extract_param_blocks(text: str) -> tuple[list[tuple[str, str]], int]:
    """Find every <parameter name="X">VAL block.

    Tolerant of missing closing ``</parameter>`` tags — when an open tag
    has no matching close before the next open or the end of string, the
    content is taken up to that boundary. Returns the list of
    ``(name, value)`` pairs plus the start offset of the FIRST opening
    tag (so callers can keep the prefix and drop everything after).
    """
    opens = list(_PARAM_OPEN_RE.finditer(text))
    if not opens:
        return [], len(text)

    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(opens):
        name = match.group(1)
        content_start = match.end()
        next_open_start = opens[index + 1].start() if index + 1 < len(opens) else len(text)
        close_match = _PARAM_CLOSE_RE.search(text, content_start, next_open_start)
        content_end = close_match.start() if close_match else next_open_start
        blocks.append((name, text[content_start:content_end]))

    return blocks, opens[0].start()


@dataclass
class TheoryRepair:
    theory_id: str
    title: str
    extracted: dict[str, Any]
    cleaned_fields: dict[str, str]  # source_column -> cleaned value
    skipped: list[str]


# Source columns the leak can land in. Each holds free-text the agent
# wrote; any of them can carry trailing <parameter ...> garbage.
_SCAN_SOURCES: tuple[str, ...] = ("claim", "experiment_plan")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="workspace_id to scan")
    parser.add_argument("--db-path", required=True, help="path to memory.db")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument(
        "--backup-first",
        action="store_true",
        help="copy the DB to memory.db.bak-theory-repair-<ts> before --apply",
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    return parser.parse_args(argv)


def _fetch_candidates(conn: sqlite3.Connection, workspace_id: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, title, claim, mechanism, predictions_json, validation_criteria_json,
               experiment_plan, tags_json, dependent_decision_ids_json
        FROM theories
        WHERE workspace_id = ?
          AND (
              claim           LIKE '%<parameter%'
           OR claim           LIKE '%</claim>%'
           OR experiment_plan LIKE '%<parameter%'
          )
        """,
        (workspace_id,),
    ).fetchall()
    return list(rows)


def _column_is_empty(row: sqlite3.Row, column: str) -> bool:
    value = row[column]
    if value is None or value == "":
        return True
    if column in _LIST_COLUMNS:
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            return False
        return parsed in (None, [], "")
    return False


def _coerce(column: str, raw: str) -> Any:
    """Coerce extracted XML text to the column's expected JSON shape."""
    text = raw.strip()
    if column in _LIST_COLUMNS:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        # Fall back to a single-element list so we never lose content.
        return [text] if text else []
    return text


def _plan_repair(row: sqlite3.Row) -> TheoryRepair | None:
    extracted: dict[str, Any] = {}
    skipped: list[str] = []
    cleaned_fields: dict[str, str] = {}

    for source in _SCAN_SOURCES:
        raw = row[source] or ""
        blocks, first_open_start = _extract_param_blocks(raw)
        if not blocks:
            continue
        for name, value in blocks:
            column = _EMBEDDED_FIELDS.get(name)
            if column is None:
                skipped.append(f"{source}:{name}")
                continue
            if column == source:
                # The block reports its own column — no move needed,
                # we'll just trim the source after the open tag.
                skipped.append(f"{source}:{name}:self")
                continue
            if not _column_is_empty(row, column):
                skipped.append(f"{source}:{name}:column-already-set")
                continue
            extracted[column] = _coerce(column, value)
        cleaned = raw[:first_open_start]
        cleaned = _TRAILING_TAG_RE.sub("", cleaned)
        cleaned = cleaned.rstrip()
        if cleaned != raw.rstrip():
            cleaned_fields[source] = cleaned

    if not extracted and not cleaned_fields:
        return None

    return TheoryRepair(
        theory_id=row["id"],
        title=row["title"] or "",
        extracted=extracted,
        cleaned_fields=cleaned_fields,
        skipped=skipped,
    )


def _apply_repair(conn: sqlite3.Connection, repair: TheoryRepair) -> None:
    set_clauses: list[str] = []
    params: list[Any] = []
    for source, cleaned in repair.cleaned_fields.items():
        set_clauses.append(f"{source} = ?")
        params.append(cleaned)
    for column, value in repair.extracted.items():
        if column in _LIST_COLUMNS:
            set_clauses.append(f"{column} = ?")
            params.append(json.dumps(value))
        else:
            set_clauses.append(f"{column} = ?")
            params.append(value)
    if not set_clauses:
        return
    params.append(repair.theory_id)
    conn.execute(
        f"UPDATE theories SET {', '.join(set_clauses)} WHERE id = ?",
        params,
    )


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"FATAL: db not found: {db_path}", file=sys.stderr)
        return 2

    if args.apply and args.backup_first:
        ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
        backup = db_path.with_suffix(db_path.suffix + f".bak-theory-repair-{ts}")
        shutil.copy2(db_path, backup)
        if not args.json:
            print(f"backup: {backup}")

    conn = sqlite3.connect(str(db_path))
    try:
        candidates = _fetch_candidates(conn, args.workspace)
        repairs = [r for r in (_plan_repair(row) for row in candidates) if r]

        report = {
            "db_path": str(db_path),
            "workspace_id": args.workspace,
            "candidates_scanned": len(candidates),
            "repairs_planned": len(repairs),
            "repairs": [
                {
                    "theory_id": r.theory_id,
                    "title": r.title,
                    "extracted_fields": list(r.extracted.keys()),
                    "cleaned_sources": list(r.cleaned_fields.keys()),
                    "skipped_blocks": r.skipped,
                }
                for r in repairs
            ],
            "applied": False,
        }

        if args.apply:
            for repair in repairs:
                _apply_repair(conn, repair)
            conn.commit()
            report["applied"] = True

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"workspace: {args.workspace}")
            print(f"db: {db_path}")
            print(f"candidates scanned: {report['candidates_scanned']}")
            print(f"repairs planned: {report['repairs_planned']}")
            for entry in report["repairs"]:
                print(
                    f"  {entry['theory_id']}: extract {entry['extracted_fields']} "
                    f"(skipped {entry['skipped_blocks']})"
                )
            print(f"applied: {report['applied']}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
