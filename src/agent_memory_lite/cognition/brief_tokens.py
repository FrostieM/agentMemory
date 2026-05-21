"""Token approximation helpers for the brief composer.

Extracted from cognition/brief.py during the v3.7 SLOC decomposition.
"""

from __future__ import annotations


def approx_tokens(text: str) -> int:
    """Whitespace-split token count. Close enough to cl100k for budgeting."""
    if not text:
        return 0
    return len(text.split())


def fit_to_budget(lines: list[str], budget: int) -> list[str]:
    """Keep lines that fit within ``budget``; skip oversized lines and keep going.

    Earlier semantics broke on the first overflowing line, which meant a
    single oversized line (e.g. a long self-model narrative) silently
    nuked every subsequent line in the section. The new semantics:
    skip the line that doesn't fit and try the next -- shorter trailing
    lines (e.g. workspace overview + discipline reminder) still render.
    Lines are kept in input order; the only difference is that overflow
    no longer terminates the loop.
    """
    out: list[str] = []
    used = 0
    for line in lines:
        cost = approx_tokens(line)
        if used + cost > budget:
            continue
        out.append(line)
        used += cost
    return out
