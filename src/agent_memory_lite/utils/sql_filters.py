"""Reusable SQL filter snippets shared by repositories.

These helpers stay tiny and stateless so any repo can compose them
into a SELECT without leaking parameter handling. They are not a
query-builder; the goal is to avoid copy-pasting the same
``AND created_at >= ?`` boilerplate across every list endpoint.
"""

from __future__ import annotations


def date_range_clause(
    *,
    since: str | None,
    until: str | None,
    column: str = "created_at",
) -> tuple[str, list[str]]:
    """Return ``(sql_fragment, params)`` for an optional date range.
    Both endpoints are ISO strings; missing endpoints relax that side.
    The fragment always starts with ``AND`` so it can be appended to a
    WHERE that already has at least one predicate."""
    fragments: list[str] = []
    params: list[str] = []
    if since:
        fragments.append(f"AND {column} >= ?")
        params.append(since)
    if until:
        fragments.append(f"AND {column} <= ?")
        params.append(until)
    return (" ".join(fragments), params)
