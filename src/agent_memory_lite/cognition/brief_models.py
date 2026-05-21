"""Brief domain dataclasses — one render-able section + the final brief.

Extracted from cognition/brief.py during the v3.7 SLOC decomposition;
imported back into the brief.py facade so existing import sites are
unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class BriefSection:
    """One render-able section of the brief with its budget + lines."""

    name: str
    budget: int
    lines: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Brief:
    """Final brief — markdown body + composition stats."""

    body_md: str
    token_count: int
    sections: list[BriefSection]
    cache_hit: bool = False
