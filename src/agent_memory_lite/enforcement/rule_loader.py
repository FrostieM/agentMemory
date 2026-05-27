"""Load active rules for PreToolUse enforcement.

Default: every active **pinned** ``behavior_instruction`` is enforced.
The PreToolUse hook treats pinning as the operator's signal that the
rule is load-bearing and must not be skipped under task pressure.

Classification (decides which dispatcher layer judges the rule):

* If ``applies_to`` lists a known ``mechanical:<detector>`` tag → routed
  to the mechanical layer (regex + session trail, ~ms).
* Otherwise → routed to the semantic layer (Ollama judge, ~seconds).

Opt-OUT via ``enforcement:none`` in ``applies_to``: the rule stays a
foreground reminder and is never sent to either dispatcher. Reserved
for subjective rules ("write clean code") where an LLM judge would
generate false positives that block legitimate work.

Legacy ``enforcement:mechanical`` / ``enforcement:semantic`` tags are
still accepted: they explicitly pin a rule to a layer regardless of
the auto-classification.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

MECHANICAL_TAG = "enforcement:mechanical"
SEMANTIC_TAG = "enforcement:semantic"
OPT_OUT_TAG = "enforcement:none"
_KNOWN_MECHANICAL_TAGS = frozenset(
    {
        "mechanical:no-magic-number",
        "mechanical:decision-provenance",
        "mechanical:read-before-edit",
        "mechanical:search-before-arch",
    }
)


@dataclass(frozen=True, slots=True)
class EnforcementRule:
    """One active enforcement-targeted behavior_instruction."""

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


def _has_mechanical_detector(applies_to: list[str]) -> bool:
    return any(tag in _KNOWN_MECHANICAL_TAGS for tag in applies_to)


def _classify(applies_to: list[str]) -> str | None:
    """Return ``mechanical`` / ``semantic`` / None (opt-out).

    Order of precedence:
      1. ``OPT_OUT_TAG`` → None (rule stays reminder-only).
      2. Explicit ``MECHANICAL_TAG`` → mechanical.
      3. Explicit ``SEMANTIC_TAG`` → semantic.
      4. Any known ``mechanical:<detector>`` tag → mechanical.
      5. Default → semantic (Ollama-judged).
    """
    if OPT_OUT_TAG in applies_to:
        return None
    if MECHANICAL_TAG in applies_to:
        return "mechanical"
    if SEMANTIC_TAG in applies_to:
        return "semantic"
    if _has_mechanical_detector(applies_to):
        return "mechanical"
    return "semantic"


def load_enforcement_rules(conn: sqlite3.Connection, workspace_id: str) -> list[EnforcementRule]:
    """Return active pinned rules eligible for enforcement.

    Pinned = the operator-marked subset of behaviors that
    rides every active envelope. Pinning is the contract that says
    "this rule is load-bearing"; the PreToolUse hook treats every
    pinned rule as enforceable by default.

    Failure-soft on pre-v3 / fresh v3 DBs where ``behaviors``
    has not been created -- returns empty list so the surrounding lint
    pipeline can still evaluate reflexes and discipline hints.
    """
    try:
        rows = conn.execute(
            """
            SELECT id, name, rule, applies_to_json
            FROM behaviors
            WHERE workspace_id = ? AND active = 1 AND pinned = 1
            ORDER BY updated_at DESC
            """,
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
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
