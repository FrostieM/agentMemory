"""Stopword list for the capability_suggester (Move 3 of v2.2).

Lifted into its own module so the suggester proper stays under the
SLOC ceiling. Compact one-line list source — keeps the actual stopword
set readable as a single sentence.
"""

from __future__ import annotations

_RAW = (
    "the and for with that this from into use uses using used will should "
    "would could are was were been being has have had but not any all may "
    "must can via when what which who how why than then their they them "
    "its our ours your yours".split()
)
STOPWORDS: frozenset[str] = frozenset(_RAW)
