"""Heuristic regex patterns for v1.10 correction detection.

Two complementary pattern groups detect when a user message corrects the
agent's previous claim:

* ``CORRECTION_OPENERS`` — short top-of-message tokens ("нет, ...",
  "no, ...", "wait,", "actually,") that signal contradiction in the
  first 1-2 words. High specificity.
* ``CORRECTION_BODY`` — mid-message phrases ("это не так", "ты не прав",
  "он не мог", "that's not", "you're wrong") that signal contradiction
  even without an explicit opener. Lower specificity but catches
  Russian-style corrections that don't lead with "нет".

A negative filter ``LIKELY_AGREEMENT`` rejects short messages that
LOOK like contradiction but are actually agreement-with-negation
("нет проблем" = "no problem", not a correction).

The patterns are isolated in this module (rather than inlined into
``correction_extractor.py``) so unit tests can exercise them directly
with hypothesis-style property tests, and so future tuning happens in
one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CORRECTION_OPENERS = re.compile(
    r"(?im)^\s*(?:"
    # Russian top-of-message contradiction. Punctuation class includes
    # em-dash (—) and en-dash (–) so "нет — это не так" matches as an
    # opener; without them only `нет, ...` / `нет. ...` would.
    r"нет(?:[\s,.—–])"
    r"|неправильн|неверн|поправляю|поправь"
    # First-person fact-evidence counter that uses an intensifier.
    # Catches "я буквально его запустил..." — a Russian construction
    # where the user offers temporal counter-evidence to the agent's
    # claim without an explicit "нет". The intensifier
    # ("буквально"/"только что") is the signal that the message is
    # a counter-claim, not a neutral statement.
    r"|я\s+(?:буквально|только\s+что|сам(?:а)?\s+(?:буквально|только\s+что))"
    # English equivalents
    r"|no(?:[\s,.])"
    r"|wait(?:[\s,.])"
    r"|actually(?:[\s,.])"
    r"|i\s+(?:literally|just)\s+\w"
    r"|that(?:'s|\s+is|\s+was)\s+(?:not|wrong|incorrect)"
    r"|you(?:'re|\s+are)\s+wrong"
    r")"
)

CORRECTION_BODY = re.compile(
    r"(?im)(?:"
    # Russian body markers — match anywhere in the message
    r"это не так|это не\s+\w"
    r"|ты не прав|был(?:а)?\s+неправ"
    # generic "<subject> не мог/могла/могло/могли" — covers "MCP не мог",
    # "сервер не мог", "оно не могло", any subject before the verb
    r"|\bне\s+мог(?:ла|ло|ли)?\b"
    # English equivalents — apostrophe optional so "doesnt" / "isnt" /
    # "cant" all match alongside the contracted forms
    r"|that\s+(?:doesn'?t|does not|isn'?t|is not)"
    r"|that's\s+(?:not|incorrect|wrong)"
    r"|\bcan'?(?:t|not)\b"
    r")"
)

# Negative filter: short messages that look contradictory but are actually
# agreement-with-negation phrases. Restricted to short whole-message form.
LIKELY_AGREEMENT = re.compile(
    r"(?im)^\s*(?:no problem|нет проблем|no thanks|нет,?\s+спасибо|nope|nah)"
    r"\.?\s*$"
)


@dataclass(frozen=True, slots=True)
class CorrectionMatch:
    """Result of matching the correction pattern set against a user message.

    ``confidence`` is the detector's confidence the message is a correction:
    * 0.85 if both opener and body matched (high signal),
    * 0.70 if only opener matched (top-of-message contradiction),
    * 0.50 if only body matched (mid-message contradiction without opener),
    * 0.0 if no match or filtered as agreement.

    ``matched`` is True when confidence >= ``min_confidence``.
    """

    matched: bool
    confidence: float
    opener_match: str | None
    body_match: str | None
    rejected_as_agreement: bool


def match_correction(
    user_message: str,
    *,
    min_user_len: int = 30,
    min_confidence: float = 0.5,
) -> CorrectionMatch:
    """Score a user message against the correction pattern set.

    The function is pure: same input → same output. Length thresholds
    short-circuit before regex work; agreement filter takes precedence over
    pattern matches so "нет проблем." is never classified as a correction.
    """
    text = user_message or ""
    if len(text.strip()) < min_user_len:
        return CorrectionMatch(False, 0.0, None, None, False)

    if LIKELY_AGREEMENT.search(text):
        return CorrectionMatch(False, 0.0, None, None, True)

    opener = CORRECTION_OPENERS.search(text)
    body = CORRECTION_BODY.search(text)

    if opener and body:
        confidence = 0.85
    elif opener:
        confidence = 0.70
    elif body:
        confidence = 0.50
    else:
        confidence = 0.0

    return CorrectionMatch(
        matched=confidence >= min_confidence,
        confidence=confidence,
        opener_match=opener.group(0) if opener else None,
        body_match=body.group(0) if body else None,
        rejected_as_agreement=False,
    )
