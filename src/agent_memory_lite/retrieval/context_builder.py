"""Final context block builder.

Produces the XML-like envelope the agent consumes:

    <memory_context>
      <core_memory>...</core_memory>
      <task_state>...</task_state>
      <active_decisions>...</active_decisions>
      <active_theories>...</active_theories>
      <research_agenda>...</research_agenda>
      <agent_capabilities>...</agent_capabilities>
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
from agent_memory_lite.models.capabilities import (
    AgentCapabilities,
    AgentPlaybook,
    AgentRole,
    AgentSkill,
)
from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.core_memory import CoreMemory
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.enums import CapabilityLinkTargetType
from agent_memory_lite.models.procedural import ProceduralRule
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.models.retrieval import (
    RetrievalCandidate,
    RetrievalQuery,
    ScoredHit,
)
from agent_memory_lite.models.task_state import TaskState
from agent_memory_lite.models.theories import Theory, TheoryEvidence
from agent_memory_lite.repositories.capabilities_repo import build_agent_capabilities
from agent_memory_lite.repositories.capability_links_repo import list_capability_links_for_targets
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
from agent_memory_lite.utils.tokens import estimate_tokens
from agent_memory_lite.vector_store.base import VectorStore

MAX_FTS_HITS = 30
MAX_VECTOR_HITS = 30
MAX_GRAPH_HITS = 40
MAX_DECISIONS = 4
MAX_HISTORICAL_DECISIONS = 20
MAX_THEORIES = 2
MAX_THEORY_EVIDENCE = 1
MAX_RESEARCH_AGENDA = 2
MAX_AGENT_CAPABILITIES = 1
MAX_TITLE_CHARS = 180
MAX_TEXT_CHARS = 280
MAX_COMMAND_CHARS = 180
MAX_LIST_ITEMS = 1
MAX_LIST_ITEM_CHARS = 140
MAX_CHUNK_TEXT_CHARS = 1200
MIN_CHUNK_RESERVE_TOKENS = 384
MAX_CHUNK_RESERVE_TOKENS = 1200
STRUCTURED_SAFETY_RESERVE_TOKENS = 128


@dataclass(frozen=True, slots=True)
class TheoryContext:
    theory: Theory
    evidence: list[TheoryEvidence] = field(default_factory=list)
    capability_links: list[CapabilityLink] = field(default_factory=list)


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
    agent_capabilities: AgentCapabilities | None = None
    rules: list[ProceduralRule] = field(default_factory=list)


def _clip_text(text: str, max_chars: int) -> str:
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


def _clip_hits_for_context(hits: list[ScoredHit]) -> list[ScoredHit]:
    return [
        hit.model_copy(update={"text": _clip_text(hit.text, MAX_CHUNK_TEXT_CHARS)}) for hit in hits
    ]


def _chunk_reserve_tokens(max_tokens: int, *, has_hits: bool) -> int:
    if not has_hits:
        return min(STRUCTURED_SAFETY_RESERVE_TOKENS, max_tokens)
    return min(MAX_CHUNK_RESERVE_TOKENS, max(MIN_CHUNK_RESERVE_TOKENS, max_tokens // 3))


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
    lines.append(f"    <goal>{escape(_clip_text(task.goal, MAX_TEXT_CHARS))}</goal>")
    lines.append(f"    <status>{escape(task.status)}</status>")
    if task.next_action:
        lines.append(
            f"    <next_action>{escape(_clip_text(task.next_action, MAX_TEXT_CHARS))}</next_action>"
        )
    if task.blockers:
        lines.append("    <blockers>")
        visible, omitted = _limited_items(task.blockers)
        for item in visible:
            lines.append(f"      <item>{escape(item)}</item>")
        lines.extend(_render_omitted_line(count=omitted, tag="omitted", indent="      "))
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
        lines.append(f"      <title>{escape(_clip_text(item.title, MAX_TITLE_CHARS))}</title>")
        lines.append(f"      <text>{escape(_clip_text(item.decision_text, MAX_TEXT_CHARS))}</text>")
        lines.append("    </decision>")
    lines.append("  </active_decisions>")
    return lines


def _theory_attrs(item: Theory) -> str:
    return (
        f"id={quoteattr(item.id)} "
        f"status={quoteattr(item.status.value)} "
        f"domain={quoteattr(item.domain)} "
        f"confidence={quoteattr(f'{item.confidence:.2f}')} "
        f"importance={quoteattr(f'{item.importance:.2f}')} "
        f"evidence_count={quoteattr(str(item.evidence_count))} "
        f"evidence_strength={quoteattr(f'{item.evidence_strength:.2f}')} "
        f"source={quoteattr(item.source_episode_id or '')}"
    )


def _render_string_items(
    *,
    container_tag: str,
    item_tag: str,
    items: list[str],
    indent: str,
) -> list[str]:
    if not items:
        return []
    lines = [f"{indent}<{container_tag}>"]
    visible, omitted = _limited_items(items)
    for item in visible:
        lines.append(f"{indent}  <{item_tag}>{escape(item)}</{item_tag}>")
    lines.extend(_render_omitted_line(count=omitted, tag="omitted", indent=f"{indent}  "))
    lines.append(f"{indent}</{container_tag}>")
    return lines


def _render_theory_evidence(items: list[TheoryEvidence]) -> list[str]:
    if not items:
        return []
    lines = ["      <evidence>"]
    for evidence in items:
        ev_attrs = (
            f"id={quoteattr(evidence.id)} "
            f"kind={quoteattr(evidence.kind.value)} "
            f"confidence={quoteattr(f'{evidence.confidence:.2f}')} "
            f"observed_at={quoteattr(evidence.observed_at)} "
            f"source={quoteattr(evidence.source_episode_id or '')}"
        )
        lines.append(
            f"        <item {ev_attrs}>{escape(_clip_text(evidence.summary, MAX_TEXT_CHARS))}</item>"
        )
    lines.append("      </evidence>")
    return lines


def _render_capability_links(items: list[CapabilityLink]) -> list[str]:
    if not items:
        return []
    lines = ["      <capability_links>"]
    for link in items:
        attrs = (
            f"id={quoteattr(link.id)} "
            f"capability_type={quoteattr(link.capability_type.value)} "
            f"capability_id={quoteattr(link.capability_id)} "
            f"relation={quoteattr(link.relation.value)} "
            f"strength={quoteattr(f'{link.strength:.2f}')} "
            f"source={quoteattr(link.source_episode_id or '')}"
        )
        lines.append(f"        <link {attrs}>")
        lines.append(
            f"          <name>{escape(_clip_text(link.capability_name, MAX_TITLE_CHARS))}</name>"
        )
        if link.rationale:
            lines.append(
                f"          <rationale>{escape(_clip_text(link.rationale, MAX_TEXT_CHARS))}</rationale>"
            )
        lines.append("        </link>")
    lines.append("      </capability_links>")
    return lines


def _render_theory(bundle: TheoryContext) -> list[str]:
    item = bundle.theory
    lines = [f"    <theory {_theory_attrs(item)}>"]
    lines.append(f"      <title>{escape(_clip_text(item.title, MAX_TITLE_CHARS))}</title>")
    lines.append(f"      <claim>{escape(_clip_text(item.claim, MAX_TEXT_CHARS))}</claim>")
    if item.mechanism:
        lines.append(
            f"      <mechanism>{escape(_clip_text(item.mechanism, MAX_TEXT_CHARS))}</mechanism>"
        )
    lines.extend(
        _render_string_items(
            container_tag="predictions",
            item_tag="item",
            items=item.predictions,
            indent="      ",
        )
    )
    lines.extend(
        _render_string_items(
            container_tag="validation_criteria",
            item_tag="item",
            items=item.validation_criteria,
            indent="      ",
        )
    )
    if item.experiment_plan:
        lines.append(
            f"      <experiment_plan>{escape(_clip_text(item.experiment_plan, MAX_TEXT_CHARS))}</experiment_plan>"
        )
    lines.extend(
        _render_string_items(
            container_tag="dependent_decisions",
            item_tag="decision_id",
            items=item.dependent_decision_ids,
            indent="      ",
        )
    )
    lines.extend(
        _render_string_items(
            container_tag="tags",
            item_tag="tag",
            items=item.tags,
            indent="      ",
        )
    )
    lines.extend(_render_theory_evidence(bundle.evidence))
    lines.extend(_render_capability_links(bundle.capability_links))
    lines.append("    </theory>")
    return lines


def _render_theories(items: list[TheoryContext]) -> list[str]:
    if not items:
        return ["  <active_theories/>"]
    lines = ["  <active_theories>"]
    for bundle in items:
        lines.extend(_render_theory(bundle))
    lines.append("  </active_theories>")
    return lines


def _render_research_agenda(agenda: ResearchAgenda | None) -> list[str]:
    return _render_research_agenda_with_links(agenda, experiment_links={}, insight_links={})


def _render_research_agenda_with_links(
    agenda: ResearchAgenda | None,
    *,
    experiment_links: dict[str, list[CapabilityLink]],
    insight_links: dict[str, list[CapabilityLink]],
) -> list[str]:
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
        lines.append(
            f"      <title>{escape(_clip_text(experiment.title, MAX_TITLE_CHARS))}</title>"
        )
        lines.append(
            f"      <hypothesis>{escape(_clip_text(experiment.hypothesis, MAX_TEXT_CHARS))}</hypothesis>"
        )
        if experiment.cohort_definition:
            lines.append(
                f"      <cohort>{escape(_clip_text(experiment.cohort_definition, MAX_TEXT_CHARS))}</cohort>"
            )
        if experiment.success_criteria:
            criteria = json.dumps(experiment.success_criteria, sort_keys=True)
            lines.append(
                f"      <success_criteria>{escape(_clip_text(criteria, MAX_COMMAND_CHARS))}</success_criteria>"
            )
        if experiment.command:
            lines.append(
                f"      <command>{escape(_clip_text(experiment.command, MAX_COMMAND_CHARS))}</command>"
            )
        lines.extend(_render_capability_links(experiment_links.get(experiment.id, [])))
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
        lines.append(
            f"      <summary>{escape(_clip_text(insight.summary, MAX_TEXT_CHARS))}</summary>"
        )
        if insight.proposed_action:
            lines.append(
                f"      <proposed_action>{escape(_clip_text(insight.proposed_action, MAX_TEXT_CHARS))}</proposed_action>"
            )
        lines.extend(_render_capability_links(insight_links.get(insight.id, [])))
        lines.append("    </insight>")

    for concept in agenda.concepts:
        attrs = (
            f"id={quoteattr(concept.id)} "
            f"kind={quoteattr(concept.kind.value)} "
            f"confidence={quoteattr(f'{concept.confidence:.2f}')}"
        )
        lines.append(f"    <concept {attrs}>")
        lines.append(f"      <name>{escape(_clip_text(concept.name, MAX_TITLE_CHARS))}</name>")
        lines.append(
            f"      <definition>{escape(_clip_text(concept.definition, MAX_TEXT_CHARS))}</definition>"
        )
        lines.append("    </concept>")

    for snapshot in agenda.snapshots:
        attrs = (
            f"id={quoteattr(snapshot.id)} "
            f"key={quoteattr(snapshot.snapshot_key)} "
            f"source={quoteattr(snapshot.source)} "
            f"total_rows={quoteattr(str(snapshot.total_rows))}"
        )
        lines.append(f"    <snapshot {attrs}>")
        lines.append(f"      <title>{escape(_clip_text(snapshot.title, MAX_TITLE_CHARS))}</title>")
        if snapshot.duckdb_path:
            lines.append(
                f"      <duckdb_path>{escape(_clip_text(snapshot.duckdb_path, MAX_COMMAND_CHARS))}</duckdb_path>"
            )
        lines.append("    </snapshot>")

    lines.append("  </research_agenda>")
    return lines


def _capability_attrs(
    *,
    item_id: str,
    confidence: float,
    source_episode_id: str | None,
) -> str:
    return (
        f"id={quoteattr(item_id)} "
        f"confidence={quoteattr(f'{confidence:.2f}')} "
        f"source={quoteattr(source_episode_id or '')}"
    )


def _render_item_list(
    *,
    container_tag: str,
    item_tag: str,
    items: list[str],
    indent: str = "      ",
) -> list[str]:
    if not items:
        return []
    lines = [f"{indent}<{container_tag}>"]
    for item in items:
        lines.append(f"{indent}  <{item_tag}>{escape(item)}</{item_tag}>")
    lines.append(f"{indent}</{container_tag}>")
    return lines


def _render_role(role: AgentRole) -> list[str]:
    attrs = _capability_attrs(
        item_id=role.id,
        confidence=role.confidence,
        source_episode_id=role.source_episode_id,
    )
    lines = [f"    <role {attrs}>"]
    lines.append(f"      <name>{escape(_clip_text(role.name, MAX_TITLE_CHARS))}</name>")
    lines.append(f"      <purpose>{escape(_clip_text(role.purpose, MAX_TEXT_CHARS))}</purpose>")
    lines.extend(
        _render_item_list(
            container_tag="responsibilities",
            item_tag="item",
            items=role.responsibilities,
        )
    )
    lines.append("    </role>")
    return lines


def _render_skill(skill: AgentSkill) -> list[str]:
    attrs = _capability_attrs(
        item_id=skill.id,
        confidence=skill.confidence,
        source_episode_id=skill.source_episode_id,
    )
    lines = [f"    <skill {attrs}>"]
    lines.append(f"      <name>{escape(_clip_text(skill.name, MAX_TITLE_CHARS))}</name>")
    lines.append(f"      <summary>{escape(_clip_text(skill.summary, MAX_TEXT_CHARS))}</summary>")
    lines.extend(
        _render_item_list(container_tag="when_to_use", item_tag="item", items=skill.when_to_use)
    )
    lines.append("    </skill>")
    return lines


def _render_playbook(playbook: AgentPlaybook) -> list[str]:
    attrs = _capability_attrs(
        item_id=playbook.id,
        confidence=playbook.confidence,
        source_episode_id=playbook.source_episode_id,
    )
    lines = [f"    <playbook {attrs}>"]
    lines.append(f"      <name>{escape(_clip_text(playbook.name, MAX_TITLE_CHARS))}</name>")
    lines.append(f"      <goal>{escape(_clip_text(playbook.goal, MAX_TEXT_CHARS))}</goal>")
    lines.extend(_render_item_list(container_tag="steps", item_tag="step", items=playbook.steps))
    lines.append("    </playbook>")
    return lines


def _render_agent_capabilities(capabilities: AgentCapabilities | None) -> list[str]:
    if capabilities is None:
        return ["  <agent_capabilities/>"]
    if not capabilities.roles and not capabilities.skills and not capabilities.playbooks:
        return ["  <agent_capabilities/>"]

    lines = ["  <agent_capabilities>"]
    for role in capabilities.roles:
        lines.extend(_render_role(role))
    for skill in capabilities.skills:
        lines.extend(_render_skill(skill))
    for playbook in capabilities.playbooks:
        lines.extend(_render_playbook(playbook))
    lines.append("  </agent_capabilities>")
    return lines


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


def _render(
    *,
    core: list[CoreMemory],
    task: TaskState | None,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    research_experiment_links: dict[str, list[CapabilityLink]],
    research_insight_links: dict[str, list[CapabilityLink]],
    agent_capabilities: AgentCapabilities | None,
    rules: list[ProceduralRule],
    facts: list[RetrievalCandidate],
    hits: list[ScoredHit],
) -> str:
    lines = ["<memory_context>"]
    lines.extend(_render_core(core))
    lines.extend(_render_task(task))
    lines.extend(_render_decisions(decisions))
    lines.extend(_render_theories(theories))
    lines.extend(
        _render_research_agenda_with_links(
            research_agenda,
            experiment_links=research_experiment_links,
            insight_links=research_insight_links,
        )
    )
    lines.extend(_render_agent_capabilities(agent_capabilities))
    lines.extend(_render_rules(rules))
    lines.extend(_render_facts(facts))
    lines.extend(_render_chunks(hits))
    lines.append("</memory_context>")
    return "\n".join(lines)


def _render_structured_only(
    *,
    core: list[CoreMemory],
    task: TaskState | None,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    research_experiment_links: dict[str, list[CapabilityLink]],
    research_insight_links: dict[str, list[CapabilityLink]],
    agent_capabilities: AgentCapabilities | None,
    rules: list[ProceduralRule],
    facts: list[RetrievalCandidate],
) -> str:
    return _render(
        core=core,
        task=task,
        decisions=decisions,
        theories=theories,
        research_agenda=research_agenda,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        agent_capabilities=agent_capabilities,
        rules=rules,
        facts=facts,
        hits=[],
    )


def _fit_structured_sections(
    *,
    max_tokens: int,
    chunk_reserve_tokens: int,
    core: list[CoreMemory],
    task: TaskState | None,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    research_experiment_links: dict[str, list[CapabilityLink]],
    research_insight_links: dict[str, list[CapabilityLink]],
    agent_capabilities: AgentCapabilities | None,
    rules: list[ProceduralRule],
    facts: list[RetrievalCandidate],
) -> tuple[list[Decision], list[TheoryContext], ResearchAgenda | None, AgentCapabilities | None]:
    decision_variants = [decisions, decisions[:2], decisions[:1], []]
    theory_variants = [theories, theories[:1], []]
    agenda_variants = [research_agenda, None]
    capability_variants = [agent_capabilities, None]

    target_tokens = max(0, max_tokens - chunk_reserve_tokens)
    best = (decisions, theories, research_agenda, agent_capabilities)
    best_tokens = estimate_tokens(
        _render_structured_only(
            core=core,
            task=task,
            decisions=decisions,
            theories=theories,
            research_agenda=research_agenda,
            research_experiment_links=research_experiment_links,
            research_insight_links=research_insight_links,
            agent_capabilities=agent_capabilities,
            rules=rules,
            facts=facts,
        )
    )
    for cap in capability_variants:
        for agenda in agenda_variants:
            for theory_items in theory_variants:
                for decision_items in decision_variants:
                    text = _render_structured_only(
                        core=core,
                        task=task,
                        decisions=decision_items,
                        theories=theory_items,
                        research_agenda=agenda,
                        research_experiment_links=research_experiment_links,
                        research_insight_links=research_insight_links,
                        agent_capabilities=cap,
                        rules=rules,
                        facts=facts,
                    )
                    tokens = estimate_tokens(text)
                    if tokens < best_tokens:
                        best = (decision_items, theory_items, agenda, cap)
                        best_tokens = tokens
                    if tokens <= target_tokens:
                        return decision_items, theory_items, agenda, cap
    return best


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
    clipped_hits = _clip_hits_for_context(filtered)

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
    theory_capability_links = list_capability_links_for_targets(
        conn,
        workspace_id=query.workspace_id,
        target_type=CapabilityLinkTargetType.THEORY,
        target_ids=[theory.id for theory in theory_items],
        limit_per_target=3,
    )
    theories = [
        TheoryContext(
            theory=theory,
            evidence=list_evidence_for_theory(
                conn,
                theory.id,
                limit=MAX_THEORY_EVIDENCE,
            ),
            capability_links=theory_capability_links.get(theory.id, []),
        )
        for theory in theory_items
    ]
    research_agenda = build_research_agenda(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        limit=MAX_RESEARCH_AGENDA,
    )
    research_experiment_links = list_capability_links_for_targets(
        conn,
        workspace_id=query.workspace_id,
        target_type=CapabilityLinkTargetType.EXPERIMENT,
        target_ids=[experiment.id for experiment in research_agenda.experiments],
        limit_per_target=3,
    )
    research_insight_links = list_capability_links_for_targets(
        conn,
        workspace_id=query.workspace_id,
        target_type=CapabilityLinkTargetType.RESEARCH_INSIGHT,
        target_ids=[insight.id for insight in research_agenda.insights],
        limit_per_target=3,
    )
    agent_capabilities = build_agent_capabilities(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        include_inactive=query.historical,
        limit=MAX_AGENT_CAPABILITIES,
    )
    task = get_task_state(conn, query.workspace_id, query.task_id) if query.task_id else None

    render_decisions, render_theories, render_research_agenda, render_agent_capabilities = (
        _fit_structured_sections(
            max_tokens=query.max_tokens,
            chunk_reserve_tokens=_chunk_reserve_tokens(
                query.max_tokens,
                has_hits=bool(clipped_hits),
            ),
            core=core,
            task=task,
            decisions=decisions,
            theories=theories,
            research_agenda=research_agenda,
            research_experiment_links=research_experiment_links,
            research_insight_links=research_insight_links,
            agent_capabilities=agent_capabilities,
            rules=rules,
            facts=facts,
        )
    )
    structured_text = _render_structured_only(
        core=core,
        task=task,
        decisions=render_decisions,
        theories=render_theories,
        research_agenda=render_research_agenda,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        agent_capabilities=render_agent_capabilities,
        rules=rules,
        facts=facts,
    )
    chunk_budget = max(
        0,
        query.max_tokens - estimate_tokens(structured_text),
    )
    chunks_fit = fit_within_budget(clipped_hits, max_tokens=chunk_budget)

    text = _render(
        core=core,
        task=task,
        decisions=render_decisions,
        theories=render_theories,
        research_agenda=render_research_agenda,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        agent_capabilities=render_agent_capabilities,
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
        decisions=render_decisions,
        theories=render_theories,
        research_agenda=render_research_agenda,
        agent_capabilities=render_agent_capabilities,
        rules=rules,
    )
