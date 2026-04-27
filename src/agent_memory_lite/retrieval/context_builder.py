"""Final context block builder.

Produces the XML-like envelope the agent consumes:

    <memory_context>
      <core_memory>...</core_memory>
      <task_state>...</task_state>
      <active_decisions>...</active_decisions>
      <active_theories>...</active_theories>
      <procedural_rules>...</procedural_rules>
      <retrieved_facts>...</retrieved_facts>
      <retrieved_chunks>...</retrieved_chunks>
    </memory_context>

Sections appear in priority order. The chunk pipeline runs the FTS + vector
RRF fusion; graph facts get a separate section so their relation/temporal
metadata stays first-class in the agent's context.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.models.core_memory import CoreMemory
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.procedural import ProceduralRule
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.models.retrieval import (
    RetrievalCandidate,
    RetrievalQuery,
    ScoredHit,
)
from agent_memory_lite.models.task_state import TaskState
from agent_memory_lite.models.theories import Theory, TheoryEvidence
from agent_memory_lite.repositories.core_memory_repo import list_active_core
from agent_memory_lite.repositories.decisions_repo import (
    list_active_decisions,
    list_all_decisions,
)
from agent_memory_lite.repositories.procedural_repo import list_active_rules
from agent_memory_lite.repositories.research_repo import build_research_agenda
from agent_memory_lite.repositories.task_state_repo import get_task_state
from agent_memory_lite.repositories.theories_repo import (
    list_evidence_for_theory,
    list_theories,
)
from agent_memory_lite.retrieval.candidates_fts import collect_fts
from agent_memory_lite.retrieval.candidates_graph import collect_graph
from agent_memory_lite.retrieval.candidates_vector import collect_vector
from agent_memory_lite.retrieval.filters import filter_active
from agent_memory_lite.retrieval.fusion_rrf import reciprocal_rank_fusion
from agent_memory_lite.retrieval.normalize import NormalizedQuery, normalize
from agent_memory_lite.retrieval.scoring import score_candidates
from agent_memory_lite.retrieval.token_budget import fit_within_budget
from agent_memory_lite.vector_store.base import VectorStore

MAX_FTS_HITS = 30
MAX_VECTOR_HITS = 30
MAX_GRAPH_HITS = 40
MAX_DECISIONS = 8
MAX_HISTORICAL_DECISIONS = 20
MAX_THEORIES = 6
MAX_THEORY_EVIDENCE = 3
MAX_RESEARCH_AGENDA = 6


@dataclass(frozen=True, slots=True)
class TheoryContext:
    theory: Theory
    evidence: list[TheoryEvidence] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class BuiltContext:
    text: str
    hits: list[ScoredHit]
    facts: list[RetrievalCandidate]
    normalized: NormalizedQuery
    core: list[CoreMemory] = field(default_factory=list)
    task_state: TaskState | None = None
    decisions: list[Decision] = field(default_factory=list)
    theories: list[TheoryContext] = field(default_factory=list)
    research_agenda: ResearchAgenda | None = None
    rules: list[ProceduralRule] = field(default_factory=list)


def _gather_chunk_candidates(
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


def _render_theories(items: list[TheoryContext]) -> list[str]:
    if not items:
        return ["  <active_theories/>"]
    lines = ["  <active_theories>"]
    for bundle in items:
        item = bundle.theory
        attrs = (
            f"id={quoteattr(item.id)} "
            f"status={quoteattr(item.status.value)} "
            f"domain={quoteattr(item.domain)} "
            f"confidence={quoteattr(f'{item.confidence:.2f}')} "
            f"importance={quoteattr(f'{item.importance:.2f}')} "
            f"source={quoteattr(item.source_episode_id or '')}"
        )
        lines.append(f"    <theory {attrs}>")
        lines.append(f"      <title>{escape(item.title)}</title>")
        lines.append(f"      <claim>{escape(item.claim)}</claim>")
        if item.mechanism:
            lines.append(f"      <mechanism>{escape(item.mechanism)}</mechanism>")
        if item.predictions:
            lines.append("      <predictions>")
            for prediction in item.predictions:
                lines.append(f"        <item>{escape(prediction)}</item>")
            lines.append("      </predictions>")
        if item.experiment_plan:
            lines.append(f"      <experiment_plan>{escape(item.experiment_plan)}</experiment_plan>")
        if item.tags:
            lines.append("      <tags>")
            for tag in item.tags:
                lines.append(f"        <tag>{escape(tag)}</tag>")
            lines.append("      </tags>")
        if bundle.evidence:
            lines.append("      <evidence>")
            for evidence in bundle.evidence:
                ev_attrs = (
                    f"id={quoteattr(evidence.id)} "
                    f"kind={quoteattr(evidence.kind.value)} "
                    f"confidence={quoteattr(f'{evidence.confidence:.2f}')} "
                    f"observed_at={quoteattr(evidence.observed_at)} "
                    f"source={quoteattr(evidence.source_episode_id or '')}"
                )
                lines.append(f"        <item {ev_attrs}>{escape(evidence.summary)}</item>")
            lines.append("      </evidence>")
        lines.append("    </theory>")
    lines.append("  </active_theories>")
    return lines


def _render_research_agenda(agenda: ResearchAgenda | None) -> list[str]:
    if agenda is None:
        return ["  <research_agenda/>"]
    if (
        not agenda.snapshots
        and not agenda.experiments
        and not agenda.insights
        and not agenda.concepts
    ):
        return ["  <research_agenda/>"]

    lines = ["  <research_agenda>"]
    for experiment in agenda.experiments:
        attrs = (
            f"id={quoteattr(experiment.id)} "
            f"status={quoteattr(experiment.status.value)} "
            f"priority={quoteattr(f'{experiment.priority:.2f}')} "
            f"theory_id={quoteattr(experiment.theory_id or '')} "
            f"snapshot_id={quoteattr(experiment.snapshot_id or '')}"
        )
        lines.append(f"    <experiment {attrs}>")
        lines.append(f"      <title>{escape(experiment.title)}</title>")
        lines.append(f"      <hypothesis>{escape(experiment.hypothesis)}</hypothesis>")
        if experiment.cohort_definition:
            lines.append(f"      <cohort>{escape(experiment.cohort_definition)}</cohort>")
        if experiment.success_criteria:
            criteria = json.dumps(experiment.success_criteria, sort_keys=True)
            lines.append(f"      <success_criteria>{escape(criteria)}</success_criteria>")
        if experiment.command:
            lines.append(f"      <command>{escape(experiment.command)}</command>")
        lines.append("    </experiment>")

    for insight in agenda.insights:
        attrs = (
            f"id={quoteattr(insight.id)} "
            f"type={quoteattr(insight.insight_type.value)} "
            f"status={quoteattr(insight.status.value)} "
            f"confidence={quoteattr(f'{insight.confidence:.2f}')} "
            f"target_type={quoteattr(insight.target_type or '')} "
            f"target_id={quoteattr(insight.target_id or '')}"
        )
        lines.append(f"    <insight {attrs}>")
        lines.append(f"      <summary>{escape(insight.summary)}</summary>")
        if insight.proposed_action:
            lines.append(
                f"      <proposed_action>{escape(insight.proposed_action)}</proposed_action>"
            )
        lines.append("    </insight>")

    for concept in agenda.concepts:
        attrs = (
            f"id={quoteattr(concept.id)} "
            f"kind={quoteattr(concept.kind.value)} "
            f"confidence={quoteattr(f'{concept.confidence:.2f}')}"
        )
        lines.append(f"    <concept {attrs}>")
        lines.append(f"      <name>{escape(concept.name)}</name>")
        lines.append(f"      <definition>{escape(concept.definition)}</definition>")
        lines.append("    </concept>")

    for snapshot in agenda.snapshots:
        attrs = (
            f"id={quoteattr(snapshot.id)} "
            f"key={quoteattr(snapshot.snapshot_key)} "
            f"source={quoteattr(snapshot.source)} "
            f"total_rows={quoteattr(str(snapshot.total_rows))}"
        )
        lines.append(f"    <snapshot {attrs}>")
        lines.append(f"      <title>{escape(snapshot.title)}</title>")
        if snapshot.duckdb_path:
            lines.append(f"      <duckdb_path>{escape(snapshot.duckdb_path)}</duckdb_path>")
        lines.append("    </snapshot>")

    lines.append("  </research_agenda>")
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
        lines.append(f"    <fact {attrs}>{escape(item.text)}</fact>")
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


def _render(
    *,
    core: list[CoreMemory],
    task: TaskState | None,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    rules: list[ProceduralRule],
    facts: list[RetrievalCandidate],
    hits: list[ScoredHit],
) -> str:
    lines = ["<memory_context>"]
    lines.extend(_render_core(core))
    lines.extend(_render_task(task))
    lines.extend(_render_decisions(decisions))
    lines.extend(_render_theories(theories))
    lines.extend(_render_research_agenda(research_agenda))
    lines.extend(_render_rules(rules))
    lines.extend(_render_facts(facts))
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
    rankings = _gather_chunk_candidates(
        conn,
        query,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    fused = reciprocal_rank_fusion(rankings)
    scored = score_candidates(fused)
    filtered = filter_active(scored, historical=query.historical)
    chunks_fit = fit_within_budget(filtered, max_tokens=query.max_tokens)

    facts = collect_graph(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        limit=MAX_GRAPH_HITS,
        historical=query.historical,
    )

    core = list_active_core(conn, query.workspace_id)
    rules = list_active_rules(conn, query.workspace_id)
    if query.historical:
        decisions = list_all_decisions(
            conn,
            query.workspace_id,
            query=query.query,
            limit=MAX_HISTORICAL_DECISIONS,
        )
    else:
        decisions = list_active_decisions(
            conn,
            query.workspace_id,
            query=query.query,
            limit=MAX_DECISIONS,
        )
    theory_items = list_theories(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        limit=MAX_THEORIES,
        include_archived=query.historical,
    )
    theories = [
        TheoryContext(
            theory=theory,
            evidence=list_evidence_for_theory(
                conn,
                theory.id,
                limit=MAX_THEORY_EVIDENCE,
            ),
        )
        for theory in theory_items
    ]
    research_agenda = build_research_agenda(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        limit=MAX_RESEARCH_AGENDA,
    )
    task = get_task_state(conn, query.workspace_id, query.task_id) if query.task_id else None

    text = _render(
        core=core,
        task=task,
        decisions=decisions,
        theories=theories,
        research_agenda=research_agenda,
        rules=rules,
        facts=facts,
        hits=chunks_fit,
    )
    return BuiltContext(
        text=text,
        hits=chunks_fit,
        facts=facts,
        normalized=normalized,
        core=core,
        task_state=task,
        decisions=decisions,
        theories=theories,
        research_agenda=research_agenda,
        rules=rules,
    )
