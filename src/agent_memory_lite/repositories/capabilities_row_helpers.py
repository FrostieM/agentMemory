"""Shared row helpers for canonical capability repositories."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from agent_memory_lite.repositories.capabilities_search_helpers import contains_any, tokens_from


def body_md_from_sections(
    *, name: str, summary: str, sections: Sequence[tuple[str, Sequence[str]]]
) -> str:
    body = [f"# {name}", "", summary]
    for title, items in sections:
        if items:
            body.extend(["", f"## {title}", *[f"- {item}" for item in items]])
    return "\n".join(body).strip()


def filter_and_rank(
    items: list[Any],
    *,
    query: str | None,
    include_inactive: bool,
    limit: int,
    text_of: Callable[[Any], str],
    confidence_of: Callable[[Any], float],
    active_of: Callable[[Any], bool],
    updated_at_of: Callable[[Any], str],
) -> list[Any]:
    terms = tokens_from(query)
    if not include_inactive:
        items = [item for item in items if active_of(item)]
    items = [item for item in items if contains_any(text_of(item), terms)]
    items.sort(
        key=lambda item: (
            sum(1.0 for token in terms if token in text_of(item).lower())
            + confidence_of(item)
            + (0.25 if active_of(item) else -0.25),
            updated_at_of(item),
        ),
        reverse=True,
    )
    return items[:limit]
