"""Reusable SQL filter snippets shared by repositories.

These helpers stay tiny and stateless so any repo can compose them
into a SELECT without leaking parameter handling. They are not a
query-builder; the goal is to avoid copy-pasting the same
``AND created_at >= ?`` boilerplate across every list endpoint.
"""

from __future__ import annotations

# v3.5 audit-followup: ``date_range_clause`` interpolates ``column``
# into an f-string. Every CURRENT caller passes a hardcoded literal,
# but the parameter is typed ``str`` with no constraint — one future
# caller that wires a user-supplied sort key into the call site
# becomes a SQL-injection vector. The allow-list below pins the
# accepted column names; an unknown value raises ValueError fast and
# loud instead of silently being interpolated.
_ALLOWED_DATE_COLUMNS: frozenset[str] = frozenset(
    {
        "created_at",
        "updated_at",
        "observed_at",
        "valid_from",
        "valid_to",
        "decided_at",
        "completed_at",
        "claimed_at",
        "dismissed_at",
        "resolved_at",
        "last_retrieved_at",
        "last_tested_at",
        "due_at",
        "expires_at",
        "reviewed_at",
        "last_applied_at",
    }
)


def date_range_clause(
    *,
    since: str | None,
    until: str | None,
    column: str = "created_at",
) -> tuple[str, list[str]]:
    """Return ``(sql_fragment, params)`` for an optional date range.
    Both endpoints are ISO strings; missing endpoints relax that side.
    The fragment always starts with ``AND`` so it can be appended to a
    WHERE that already has at least one predicate.

    ``column`` is validated against ``_ALLOWED_DATE_COLUMNS`` — anything
    else raises ``ValueError`` so an accidental f-string of user input
    can never reach the SQL layer.
    """
    if column not in _ALLOWED_DATE_COLUMNS:
        raise ValueError(
            f"date_range_clause: column {column!r} is not in the allow-list. "
            "Add it to _ALLOWED_DATE_COLUMNS in sql_filters.py if it's a "
            "legitimate timestamp column."
        )
    fragments: list[str] = []
    params: list[str] = []
    if since:
        fragments.append(f"AND {column} >= ?")
        params.append(since)
    if until:
        fragments.append(f"AND {column} <= ?")
        params.append(until)
    return (" ".join(fragments), params)
