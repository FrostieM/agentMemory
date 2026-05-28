"""Small filters for deciding which insights are useful enough to surface."""

from __future__ import annotations

import re

_LOW_SIGNAL_FILE_INDEXED_RE = re.compile(
    r"\bfile[_\s-]?indexed\b|\bfiles\s+indexed\b",
    re.IGNORECASE,
)
_LOW_SIGNAL_FOLDER_INVENTORY_RE = re.compile(
    r"\b(?:src|tests|unit|e2e|scripts|migrations|docs|api)"
    r"(?:/[^\s]+)?\s+folder\s+contains\b",
    re.IGNORECASE,
)
_LOW_SIGNAL_TEST_INVENTORY_RE = re.compile(r"^tests\s+involving\b", re.IGNORECASE)
_LOW_SIGNAL_RECURRING_THEME_RE = re.compile(
    r"^Recurring theme \((?P<count>\d+) episodes?\): (?P<terms>.+)$",
    re.IGNORECASE,
)
_TOKENISH_TERM_RE = re.compile(r"^[a-z0-9_./-]{2,32}$", re.IGNORECASE)


def is_low_signal_file_indexed_insight(*texts: str | None) -> bool:
    """Return true for auto-ingest indexing placeholders, not durable insights."""
    joined = " ".join(text or "" for text in texts).strip()
    if _LOW_SIGNAL_FILE_INDEXED_RE.search(joined):
        return True
    return bool(
        len(joined.split()) <= 12
        and (
            _LOW_SIGNAL_FOLDER_INVENTORY_RE.search(joined)
            or _LOW_SIGNAL_TEST_INVENTORY_RE.search(joined)
        )
    )


def is_low_signal_recurring_theme_insight(*texts: str | None) -> bool:
    """Return true for tiny keyword-cluster summaries with no claim/action."""
    matches = [
        match
        for text in texts
        if text
        for match in [_LOW_SIGNAL_RECURRING_THEME_RE.match(text.strip())]
        if match is not None
    ]
    if not matches:
        return False
    match = matches[0]
    if int(match.group("count")) > 2:
        return False
    terms = [part.strip() for part in match.group("terms").split(",") if part.strip()]
    return len(terms) >= 5 and all(_TOKENISH_TERM_RE.match(term) for term in terms)


def is_low_signal_insight(*texts: str | None) -> bool:
    """Return true for known auto-consolidation noise classes."""
    return is_low_signal_file_indexed_insight(*texts) or is_low_signal_recurring_theme_insight(
        *texts
    )
