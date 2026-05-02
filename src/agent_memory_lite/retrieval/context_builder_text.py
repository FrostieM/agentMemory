"""Tiny text helpers reused by every context renderer.

Clipping, omitted-counter rendering, and the discover-then-fetch
``<index>`` block live here so each individual renderer module stays
focused on its section.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.retrieval.context_builder_constants import (
    INDEX_REF_TITLE_CHARS,
    INDEX_REFS_PER_SECTION,
    MAX_LIST_ITEM_CHARS,
    MAX_LIST_ITEMS,
)
from agent_memory_lite.utils.text_encoding import repair_common_mojibake


def _clip_text(text: str, max_chars: int) -> str:
    text = repair_common_mojibake(text)
    if len(text) <= max_chars:
        return text
    suffix = " ... [truncated]"
    return text[: max(0, max_chars - len(suffix))].rstrip() + suffix


def _limited_items(items: list[str]) -> tuple[list[str], int]:
    visible = [_clip_text(item, MAX_LIST_ITEM_CHARS) for item in items[:MAX_LIST_ITEMS]]
    return visible, max(0, len(items) - len(visible))


def _render_omitted_line(*, count: int, tag: str, indent: str) -> list[str]:
    if count <= 0:
        return []
    return [f"{indent}<{tag} count={quoteattr(str(count))}/>"]


def _index_ref(
    *,
    id_value: str,
    title: str,
    extra: dict[str, str] | None = None,
    indent: str,
) -> str:
    """Render a single <ref id=... title=... [k=v]*/> entry."""
    parts = [
        f"id={quoteattr(id_value)}",
        f"title={quoteattr(_clip_text(title, INDEX_REF_TITLE_CHARS))}",
    ]
    for key, value in (extra or {}).items():
        if value:
            parts.append(f"{key}={quoteattr(value)}")
    return f"{indent}<ref {' '.join(parts)}/>"


def _render_string_items(
    *,
    container_tag: str,
    item_tag: str,
    items: list[str],
    indent: str,
) -> list[str]:
    """Render a ``<container><item/></container>`` block with cap+omit accounting."""
    if not items:
        return []
    lines = [f"{indent}<{container_tag}>"]
    visible, omitted = _limited_items(items)
    for item in visible:
        lines.append(f"{indent}  <{item_tag}>{escape(item)}</{item_tag}>")
    lines.extend(_render_omitted_line(count=omitted, tag="omitted", indent=f"{indent}  "))
    lines.append(f"{indent}</{container_tag}>")
    return lines


def _render_index_block(
    *,
    full_count: int,
    long_tail: list[tuple[str, str, dict[str, str]]],
    indent: str,
    refs_cap: int = INDEX_REFS_PER_SECTION,
) -> list[str]:
    """Append a compact <index> block describing the long tail.

    ``long_tail`` items are tuples of (id, title, extra_attrs). The first
    ``refs_cap`` entries become ``<ref/>`` elements; everything past that
    becomes a ``<truncated count=.../>`` line so the agent knows the
    breadth even when the index itself can't list every id.
    """
    total_long = len(long_tail)
    if total_long <= 0:
        return []
    listed = long_tail[:refs_cap]
    hidden = max(0, total_long - len(listed))
    total = full_count + total_long
    attrs = (
        f"total={quoteattr(str(total))} "
        f"full={quoteattr(str(full_count))} "
        f"listed={quoteattr(str(len(listed)))} "
        f"hidden={quoteattr(str(hidden))}"
    )
    lines = [f"{indent}<index {attrs}>"]
    for id_value, title, extra in listed:
        lines.append(_index_ref(id_value=id_value, title=title, extra=extra, indent=f"{indent}  "))
    if hidden > 0:
        lines.append(f"{indent}  <truncated count={quoteattr(str(hidden))}/>")
    lines.append(f"{indent}</index>")
    return lines
