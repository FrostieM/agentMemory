"""Mechanical rule: magic numbers in strategy / calibrator paths.

Detector tag (must appear in the rule's ``applies_to`` for the dispatcher
to route here): ``mechanical:no-magic-number``.

Catches the class of violations the operator flagged 2026-05-15: an
agent writes ``if confidence > 0.85`` in a strategy module instead of
deriving the threshold from per-strategy adaptive logic.

Heuristic, not AST. The detector fires when:

  * The target file path looks like strategy / calibrator / tier / edge
    code (project-AGNOSTIC: matches common path fragments).
  * The diff contains a comparison ``<identifier> <op> <literal>`` where
    the identifier name carries threshold semantics (confidence,
    threshold, tier, ratio, rate, calibrat, weight, margin, edge) AND
    the literal is not one of the obvious-neutral set (0, 1, -1, 0.0,
    1.0, 0.5, 100).

False-positive escape: if the same diff defines an UPPER_SNAKE constant,
we assume the agent moved the number into a named constant and skip the
violation. This is rough but trades false-positives for a clean exit
path when the agent did the right thing.
"""

from __future__ import annotations

import re

# Numeric literal: integer or float, optionally signed. We deliberately
# do NOT match ``0``, ``1``, ``-1``, ``0.0``, ``1.0``, ``0.5``, ``100`` as
# magic — those appear as neutral guards in trading code.
RULE_TAG = "mechanical:no-magic-number"

_NEUTRAL_LITERALS = {"0", "1", "-1", "0.0", "1.0", "0.5", "100", "-1.0"}

_THRESHOLDED_NAMES = (
    "confidence",
    "threshold",
    "tier",
    "ratio",
    "rate",
    "calibrat",
    "weight",
    "margin",
    "edge",
    "score",
)

_PATH_FRAGMENTS = (
    "/strategy",
    "/strategies",
    "/calibrat",
    "/tier_",
    "/edge_",
    "/edges/",
    "/signal",
    "/selector",
)

_COMPARE_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|>|<|==|!=)\s*(-?\d+(?:\.\d+)?)",
)

_CONST_DEF_RE = re.compile(r"^\s*[A-Z][A-Z0-9_]{2,}\s*[:=]", re.MULTILINE)


def _path_matches(file_path: str) -> bool:
    lower = file_path.lower().replace("\\", "/")
    return any(part in lower for part in _PATH_FRAGMENTS)


def _identifier_carries_threshold_semantics(identifier: str) -> bool:
    lower = identifier.lower()
    return any(name in lower for name in _THRESHOLDED_NAMES)


def detect_magic_number(diff_text: str, file_path: str) -> str | None:
    """Return diagnostic string if a violation is found, else None."""
    if not _path_matches(file_path):
        return None
    if _CONST_DEF_RE.search(diff_text):
        # Diff introduces an UPPER_SNAKE constant — assume named constant
        # path and skip enforcement to avoid blocking the intended fix.
        return None
    for match in _COMPARE_RE.finditer(diff_text):
        identifier, op, literal = match.group(1), match.group(2), match.group(3)
        if literal in _NEUTRAL_LITERALS:
            continue
        if not _identifier_carries_threshold_semantics(identifier):
            continue
        return (
            f"magic number {literal} compared with '{identifier}' {op} {literal} "
            f"in {file_path}; extract to a named constant or per-strategy "
            f"adaptive function"
        )
    return None
