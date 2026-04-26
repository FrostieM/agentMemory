"""Final context block builder.

Produces the XML-like envelope the agent consumes:

    <memory_context>
      <core_memory>...</core_memory>
      <task_state>...</task_state>
      <active_decisions>...</active_decisions>
      <procedural_rules>...</procedural_rules>
      <retrieved_chunks>...</retrieved_chunks>
    </memory_context>

Sections appear in priority order. Phase 4 will append `<retrieved_facts>`
once the temporal graph lands.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.models.core_memory import CoreMemory
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.procedural import ProceduralRule
from agent_memory_lite.models.retrieval import (
    RetrievalCandidate,
    RetrievalQuery,
    ScoredHit,
)
from agent_memory_lite.models.task_state import TaskState
from agent_memory_lite.repositories.core_memory_repo import list_active_core
from agent_memory_lite.repositories.decisions_repo import (
    list_active_decisions,
    list_all_decisions,
)
from agent_memory_lite.repositories.procedural_repo import list_active_rules
from agent_memory_lite.repositories.task_state_repo import get_task_state
from agent_memory_lite.retrieval.candidates_fts import collect_fts
from agent_memory_lite.retrieval.candidates_vector import collect_vector
from agent_memory_lite.retrieval.filters import filter_active
from agent_memory_lite.retrieval.fusion_rrf import reciprocal_rank_fusion
from agent_memory_lite.retrieval.normalize import NormalizedQuery, normalize
from agent_memory_lite.retrieval.scoring import score_candidates
from agent_memory_lite.retrieval.token_budget import fit_within_budget
from agent_memory_lite.vector_store.base import VectorStore

MAX_FTS_HITS = 30
MAX_VECTOR_HITS = 30


@dataclass(frozen=True, slots=True)
class BuiltContext:
    text: str
    hits: list[ScoredHit]
    normalized: NormalizedQuery
    core: list[CoreMemory]
    task_state: TaskState | None
    decisions: list[Decision]
    rules: list[ProceduralRule]


def _gather_candidates(
    conn: sqlite3.Connection,
    query: RetrievalQuery,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> list[list[RetrievalCandidate]]:
    rankings: list[list[RetrievalCandidate]] = []
    fts = collect_fts(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        limit=MAX_FTS_HITS,
    )
    if fts:
        rankings.append(fts)
    if embedding_provider is not None and vector_store is not None:
        vec = collect_vector(
            conn,
            vector_store,
            embedding_provider,
            workspace_id=query.workspace_id,
            query=query.query,
            limit=MAX_VECTOR_HITS,
        )
        if vec:
            rankings.append(vec)
    return rankings


def _render_core(items: list[CoreMemory]) -> list[str]:
    if not items:
        return ["  <core_memory/>"]
    lines = ["  <core_memory>"]
    for item in items:
        attrs = (
            f"key={quoteattr(item.key)} "
            f"confidence={quoteattr(f'{item.confidence:.2f}')} "
            f"source={quoteattr(item.source_episode_id or '')}"
        )
        lines.append(f"    <item {attrs}>{escape(item.value)}</item>")
    lines.append("  </core_memory>")
    return lines


def _render_task(task: TaskState | None) -> list[str]:
    if task is None:
        return ["  <task_state/>"]
    lines = [f"  <task_state task_id={quoteattr(task.task_id)}>"]
    lines.append(f"    <goal>{escape(task.goal)}</goal>")
    lines.append(f"    <status>{escape(task.status)}</status>")
    if task.next_action:
        lines.append(f"    <next_action>{escape(task.next_action)}</next_action>")
    if task.blockers:
        lines.append("    <blockers>")
        for item in task.blockers:
            lines.append(f"      <item>{escape(item)}</item>")
        lines.append("    </blockers>")
    lines.append("  </task_state>")
    return lines


def _render_decisions(items: list[Decision]) -> list[str]:
    if not items:
        return ["  <active_decisions/>"]
    lines = ["  <active_decisions>"]
    for item in items:
        attrs = (
            f"id={quoteattr(item.id)} "
            f"confidence={quoteattr(f'{item.confidence:.2f}')} "
            f"source={quoteattr(item.source_episode_id or '')}"
        )
        lines.append(f"    <decision {attrs}>")
        lines.append(f"      <title>{escape(item.title)}</title>")
        lines.append(f"      <text>{escape(item.decision_text)}</text>")
        lines.append("    </decision>")
    lines.append("  </active_decisions>")
    return lines


def _render_rules(items: list[ProceduralRule]) -> list[str]:
    if not items:
        return ["  <procedural_rules/>"]
    lines = ["  <procedural_rules>"]
    for item in items:
        attrs = f"source={quoteattr(item.source_episode_id or '')}"
        lines.append(f"    <rule {attrs}>{escape(item.rule_text)}</rule>")
    lines.append("  </procedural_rules>")
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


def _render(
    *,
    core: list[CoreMemory],
    task: TaskState | None,
    decisions: list[Decision],
    rules: list[ProceduralRule],
    hits: list[ScoredHit],
) -> str:
    lines = ["<memory_context>"]
    lines.extend(_render_core(core))
    lines.extend(_render_task(task))
    lines.extend(_render_decisions(decisions))
    lines.extend(_render_rules(rules))
    lines.extend(_render_chunks(hits))
    lines.append("</memory_context>")
    return "\n".join(lines)


def build_context(
    conn: sqlite3.Connection,
    query: RetrievalQuery,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> BuiltContext:
    normalized = normalize(query.query)
    rankings = _gather_candidates(
        conn,
        query,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    fused = reciprocal_rank_fusion(rankings)
    scored = score_candidates(fused)
    filtered = filter_active(scored, historical=query.historical)
    fit = fit_within_budget(filtered, max_tokens=query.max_tokens)

    core = list_active_core(conn, query.workspace_id)
    rules = list_active_rules(conn, query.workspace_id)
    if query.historical:
        decisions = list_all_decisions(conn, query.workspace_id)
    else:
        decisions = list_active_decisions(conn, query.workspace_id)
    task = get_task_state(conn, query.workspace_id, query.task_id) if query.task_id else None

    text = _render(
        core=core,
        task=task,
        decisions=decisions,
        rules=rules,
        hits=fit,
    )
    return BuiltContext(
        text=text,
        hits=fit,
        normalized=normalized,
        core=core,
        task_state=task,
        decisions=decisions,
        rules=rules,
    )
