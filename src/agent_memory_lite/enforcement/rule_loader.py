"""Load active rules tagged for PreToolUse enforcement.

Convention: rules are stored as ``behavior_instructions`` rows. The
``applies_to`` JSON array carries a tag of the form
``enforcement:<level>`` where ``<level>`` is one of ``mechanical`` or
``semantic``. Rows without an enforcement tag stay in the foreground
``<behavior_instructions>`` envelope only and are NOT enforced here.

This convention avoids a schema migration: ``applies_to`` is already a
free-form list of tags. The PreToolUse hook reads only rows whose tag
opts in, so the new layer is reversible — drop the tag from
``applies_to`` and the rule reverts to reminder-only.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

MECHANICAL_TAG = "enforcement:mechanical"
SEMANTIC_TAG = "enforcement:semantic"
_TAGS = {MECHANICAL_TAG: "mechanical", SEMANTIC_TAG: "semantic"}


@dataclass(frozen=True, slots=True)
class EnforcementRule:
    """One active enforcement-tagged behavior_instruction."""

    id: str
    name: str
    rule: str
    level: str
    applies_to: list[str]


def _parse_applies_to(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _classify(applies_to: list[str]) -> str | None:
    """Return ``mechanical`` / ``semantic`` if a tag is present, else None.

    Mechanical wins if both tags appear — cheap layer 1 runs first and
    any block short-circuits the semantic-layer Ollama call.
    """
    if MECHANICAL_TAG in applies_to:
        return "mechanical"
    if SEMANTIC_TAG in applies_to:
        return "semantic"
    return None


def load_enforcement_rules(conn: sqlite3.Connection, workspace_id: str) -> list[EnforcementRule]:
    """Return active enforcement-tagged rules for the workspace."""
    rows = conn.execute(
        """
        SELECT id, name, rule, applies_to_json
        FROM behavior_instructions
        WHERE workspace_id = ? AND active = 1
        ORDER BY pinned DESC, updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    rules: list[EnforcementRule] = []
    for row in rows:
        applies_to = _parse_applies_to(row[3])
        level = _classify(applies_to)
        if level is None:
            continue
        rules.append(
            EnforcementRule(
                id=str(row[0]),
                name=str(row[1]),
                rule=str(row[2]),
                level=level,
                applies_to=applies_to,
            )
        )
    return rules


def filter_by_level(rules: list[EnforcementRule], level: str) -> list[EnforcementRule]:
    """Return only the rules at the requested enforcement level."""
    return [r for r in rules if r.level == level]
