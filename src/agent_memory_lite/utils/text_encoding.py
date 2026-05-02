"""Best-effort display repair for common mojibake.

This never mutates stored memory. It only improves rendered API/context output
when text was previously decoded with the wrong legacy encoding.
"""

from __future__ import annotations

_CP1252_MARKERS = ("â", "Ã", "Â")
_CP1251_MARKERS = tuple(
    bytes.fromhex(value).decode("cp1251")
    for value in (
        "d09f",
        "d0b0",
        "d0b1",
        "d0b2",
        "d0b3",
        "d0b4",
        "d0b5",
        "d0b8",
        "d0b9",
        "d0ba",
        "d0bd",
        "d0be",
        "d0bf",
        "d180",
        "d181",
        "d182",
        "d183",
        "d18b",
        "d18c",
        "e280",
    )
)


def _mojibake_score(text: str) -> int:
    score = text.count("\ufffd") * 20
    score += sum(text.count(marker) * 3 for marker in _CP1252_MARKERS)
    score += sum(text.count(marker) * 4 for marker in _CP1251_MARKERS)
    return score


def _try_repair(text: str, encoding: str) -> str | None:
    try:
        return text.encode(encoding).decode("utf-8")
    except UnicodeError:
        return None


def repair_common_mojibake(text: str) -> str:
    """Return a more readable string when a common mojibake repair is obvious."""

    original_score = _mojibake_score(text)
    if original_score == 0:
        return text

    candidates = [text]
    if any(marker in text for marker in _CP1252_MARKERS):
        for encoding in ("cp1252", "latin1"):
            repaired = _try_repair(text, encoding)
            if repaired is not None:
                candidates.append(repaired)
    if any(marker in text for marker in _CP1251_MARKERS):
        repaired = _try_repair(text, "cp1251")
        if repaired is not None:
            candidates.append(repaired)

    return min(candidates, key=_mojibake_score)
