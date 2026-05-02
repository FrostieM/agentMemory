"""Renderers for the small bottom-of-envelope sections.

``<procedural_rules>``, ``<retrieved_facts>``, ``<retrieved_chunks>``,
and the ``<context_omissions>`` block live here so the main render
orchestrator can compose them without scrolling.
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.models.procedural import ProceduralRule
from agent_memory_lite.models.retrieval import RetrievalCandidate, ScoredHit
from agent_memory_lite.retrieval.context_builder_constants import (
    MAX_TEXT_CHARS,
    MAX_TITLE_CHARS,
)
from agent_memory_lite.retrieval.context_builder_text import _clip_text


def _render_rules(items: list[ProceduralRule]) -> list[str]:
    if not items:
        return ["  <procedural_rules/>"]
    lines = ["  <procedural_rules>"]
    for item in items:
        attrs = f"source={quoteattr(item.source_episode_id or '')}"
        lines.append(
            f"    <rule {attrs}>{escape(_clip_text(item.rule_text, MAX_TEXT_CHARS))}</rule>"
        )
    lines.append("  </procedural_rules>")
    return lines


def _render_facts(items: list[RetrievalCandidate]) -> list[str]:
    if not items:
        return ["  <retrieved_facts/>"]
    lines = ["  <retrieved_facts>"]
    for item in items:
        valid_to = item.metadata.get("valid_to")
        attrs = (
            f"id={quoteattr(item.id)} "
            f"relation={quoteattr(str(item.metadata.get('relation', '')))} "
            f"confidence={quoteattr(f'{item.raw_score:.2f}')} "
            f"valid_from={quoteattr(str(item.metadata.get('valid_from', '')))} "
            f"valid_to={quoteattr(str(valid_to or ''))}"
        )
        lines.append(f"    <fact {attrs}>{escape(_clip_text(item.text, MAX_TEXT_CHARS))}</fact>")
    lines.append("  </retrieved_facts>")
    return lines


def _render_chunks(hits: list[ScoredHit]) -> list[str]:
    if not hits:
        return ["  <retrieved_chunks/>"]
    lines = ["  <retrieved_chunks>"]
    for hit in hits:
        attrs = (
            f"id={quoteattr(hit.id)} "
            f"path={quoteattr(hit.path or '')} "
            f"score={quoteattr(f'{hit.score:.4f}')} "
            f"sources={quoteattr(','.join(hit.sources))}"
        )
        lines.append(f"    <chunk {attrs}>")
        lines.append(f"      {escape(hit.text)}")
        lines.append("    </chunk>")
    lines.append("  </retrieved_chunks>")
    return lines


def _render_context_omissions(omissions: list[dict[str, Any]]) -> list[str]:
    if not omissions:
        return []
    lines = ['  <context_omissions reason="budget">']
    for item in omissions:
        attrs = (
            f"section={quoteattr(str(item.get('section', '')))} "
            f"type={quoteattr(str(item.get('type', '')))} "
            f"id={quoteattr(str(item.get('id', '')))} "
            f"relevance={quoteattr(str(item.get('relevance', 'elastic')))}"
        )
        title = str(item.get("title") or item.get("name") or "")
        lines.append(
            f"    <omission {attrs}>{escape(_clip_text(title, MAX_TITLE_CHARS))}</omission>"
        )
    lines.append("  </context_omissions>")
    return lines
