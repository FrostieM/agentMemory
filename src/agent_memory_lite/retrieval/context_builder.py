"""Final context block builder.

Produces the XML-like envelope the agent consumes:

    <memory_context>
      <core_memory>...</core_memory>
      <behavior_instructions>...</behavior_instructions>
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
from datetime import UTC
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.maintenance.usage_feedback import chunk_feedback_boosts
from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionSet
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
from agent_memory_lite.repositories.behavior_repo import build_behavior_instruction_set
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
from agent_memory_lite.utils.text_encoding import repair_common_mojibake
from agent_memory_lite.utils.time import now, parse_iso
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
MAX_BEHAVIOR_INSTRUCTIONS = 4
MAX_AGENT_CAPABILITIES = 1
MAX_TITLE_CHARS = 180
MAX_TEXT_CHARS = 280
MAX_COMMAND_CHARS = 180
MAX_LIST_ITEMS = 1
MAX_LIST_ITEM_CHARS = 140
MAX_CHUNK_TEXT_CHARS = 1200
MAX_STUB_CHUNK_TEXT_CHARS = 360
MAX_FULL_DECISION_ITEMS = 3
MIN_CHUNK_RESERVE_TOKENS = 384
MAX_CHUNK_RESERVE_TOKENS = 1200
STRUCTURED_SAFETY_RESERVE_TOKENS = 128
LOW_CONFIDENCE_STALE_SCORE = 0.50
LOW_CONFIDENCE_RECENT_SCORE = 0.35
LOW_CONFIDENCE_VECTOR_SCORE = 0.25
LOW_CONFIDENCE_STALE_DAYS = 14
KEEP_EXACT_FTS_RANK_BELOW = 3
MOJIBAKE_REPLACEMENT_MIN = 3
MOJIBAKE_REPLACEMENT_RATIO = 0.005
RENDER_LEVEL_RANK = {"none": 0, "stub": 1, "summary": 2, "full": 3}
INTENT_KEYWORDS = {
    "research": (
        "agenda",
        "cohort",
        "evidence",
        "experiment",
        "hypothesis",
        "replay",
        "research",
        "theory",
        "theories",
        "snapshot",
        "source-flip",
        "shadow",
    ),
    "capability": (
        "behavior",
        "capability",
        "instruction",
        "instructions",
        "playbook",
        "role",
        "roles",
        "skill",
        "skills",
    ),
    "architecture": (
        "api",
        "architecture",
        "decision",
        "deploy",
        "migration",
        "runtime",
        "vps",
    ),
    "incident": (
        "504",
        "feed down",
        "hang",
        "health",
        "incident",
        "timeout",
        "watchdog",
        "wedged",
    ),
}


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
    behavior_instructions: BehaviorInstructionSet | None = None
    agent_capabilities: AgentCapabilities | None = None
    rules: list[ProceduralRule] = field(default_factory=list)
    budget_diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StructuredFit:
    decisions: list[Decision]
    theories: list[TheoryContext]
    research_agenda: ResearchAgenda | None
    agent_capabilities: AgentCapabilities | None
    research_render_level: str
    capabilities_render_level: str
    sections: list[dict[str, Any]]
    omissions: list[dict[str, Any]]
    must_include: list[str]


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


def _clip_hits_for_context(hits: list[ScoredHit]) -> list[ScoredHit]:
    return [
        hit.model_copy(update={"text": _clip_text(hit.text, MAX_CHUNK_TEXT_CHARS)}) for hit in hits
    ]


def _stub_hit_for_context(hit: ScoredHit) -> ScoredHit:
    metadata = dict(hit.metadata)
    metadata["render_level"] = "stub"
    metadata["why_relevant"] = "exact fts match preserved under tight context budget"
    return hit.model_copy(
        update={
            "text": _clip_text(hit.text, MAX_STUB_CHUNK_TEXT_CHARS),
            "metadata": metadata,
        }
    )


def _ensure_exact_hit_stub(
    hits: list[ScoredHit],
    *,
    chosen: list[ScoredHit],
    max_tokens: int,
) -> list[ScoredHit]:
    exact_hits = [hit for hit in hits if _is_exact_fts_hit(hit)]
    if not exact_hits:
        return chosen
    chosen_ids = {hit.id for hit in chosen}
    if any(hit.id in chosen_ids for hit in exact_hits):
        return chosen

    stub = _stub_hit_for_context(exact_hits[0])
    rescue_budget = max(max_tokens, estimate_tokens(stub.text) + 32)
    rescued = fit_within_budget([stub, *chosen], max_tokens=rescue_budget)
    rescued_ids = {hit.id for hit in rescued}
    if stub.id in rescued_ids:
        return rescued
    return [stub, *chosen]


def _has_mojibake_noise(text: str) -> bool:
    replacements = text.count("\ufffd")
    if replacements < MOJIBAKE_REPLACEMENT_MIN:
        return False
    return replacements / max(len(text), 1) >= MOJIBAKE_REPLACEMENT_RATIO


def _hit_age_days(hit: ScoredHit) -> float | None:
    created_at = hit.metadata.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        return None
    try:
        created = parse_iso(created_at)
    except ValueError:
        return None
    current = now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return max(0.0, (current - created).total_seconds() / 86400.0)


def _is_exact_fts_hit(hit: ScoredHit) -> bool:
    rank = hit.metadata.get("fts_rank")
    return isinstance(rank, int) and 0 <= rank < KEEP_EXACT_FTS_RANK_BELOW


def _keep_context_hit(hit: ScoredHit, *, historical: bool) -> bool:
    if historical:
        keep = True
    elif _has_mojibake_noise(hit.text):
        keep = False
    elif _is_exact_fts_hit(hit) or hit.score >= LOW_CONFIDENCE_STALE_SCORE:
        keep = True
    else:
        age_days = _hit_age_days(hit)
        keep = hit.score >= LOW_CONFIDENCE_RECENT_SCORE or (
            "vector" in hit.sources and hit.score >= LOW_CONFIDENCE_VECTOR_SCORE
        )
        if age_days is not None and age_days > LOW_CONFIDENCE_STALE_DAYS:
            keep = False
    return keep


def filter_context_hits(hits: list[ScoredHit], *, historical: bool) -> list[ScoredHit]:
    """Suppress stale low-confidence chunk noise before the context budget pass.

    Exact FTS top hits are preserved even when old. Historical contexts also
    preserve all hits because the caller explicitly requested old decisions and
    evidence.
    """

    return [hit for hit in hits if _keep_context_hit(hit, historical=historical)]


def _apply_usage_feedback(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    hits: list[ScoredHit],
) -> list[ScoredHit]:
    boosts = chunk_feedback_boosts(
        conn,
        workspace_id=workspace_id,
        chunk_ids=[hit.id for hit in hits],
    )
    if not boosts:
        return hits
    adjusted: list[ScoredHit] = []
    for hit in hits:
        boost = boosts.get(hit.id, 0.0)
        if boost == 0.0:
            adjusted.append(hit)
            continue
        metadata = dict(hit.metadata)
        metadata["usage_feedback_boost"] = round(boost, 4)
        adjusted.append(hit.model_copy(update={"score": hit.score + boost, "metadata": metadata}))
    adjusted.sort(key=lambda item: item.score, reverse=True)
    return adjusted


def _chunk_reserve_tokens(max_tokens: int, *, has_hits: bool) -> int:
    if not has_hits:
        return min(STRUCTURED_SAFETY_RESERVE_TOKENS, max_tokens)
    return min(MAX_CHUNK_RESERVE_TOKENS, max(MIN_CHUNK_RESERVE_TOKENS, max_tokens // 3))


def _detect_intent(query: str) -> list[str]:
    lowered = query.lower()
    intents = [
        intent
        for intent, keywords in INTENT_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    ]
    return intents or ["general"]


def _agenda_count(agenda: ResearchAgenda | None) -> int:
    if agenda is None:
        return 0
    return (
        len(agenda.experiments)
        + len(agenda.insights)
        + len(agenda.concepts)
        + len(agenda.snapshots)
    )


def _capabilities_count(capabilities: AgentCapabilities | None) -> int:
    if capabilities is None:
        return 0
    return len(capabilities.roles) + len(capabilities.skills) + len(capabilities.playbooks)


def _research_object_ids(agenda: ResearchAgenda | None) -> list[str]:
    if agenda is None:
        return []
    return [
        *[item.id for item in agenda.experiments],
        *[item.id for item in agenda.insights],
        *[item.id for item in agenda.concepts],
        *[item.id for item in agenda.snapshots],
    ]


def _capability_object_ids(capabilities: AgentCapabilities | None) -> list[str]:
    if capabilities is None:
        return []
    return [
        *[item.id for item in capabilities.roles],
        *[item.id for item in capabilities.skills],
        *[item.id for item in capabilities.playbooks],
    ]


def _section_diag(
    *,
    name: str,
    render_level: str,
    included: int,
    omitted: int,
) -> dict[str, Any]:
    return {
        "name": name,
        "render_level": render_level,
        "objects_included": included,
        "objects_omitted": omitted,
    }


def _render_rank(level: str) -> int:
    return RENDER_LEVEL_RANK.get(level, 0)


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
    for index, item in enumerate(items):
        full_text = index < MAX_FULL_DECISION_ITEMS
        attrs = (
            f"id={quoteattr(item.id)} "
            f"confidence={quoteattr(f'{item.confidence:.2f}')} "
            f"source={quoteattr(item.source_episode_id or '')} "
            f"full_text={quoteattr(str(full_text).lower())}"
        )
        lines.append(f"    <decision {attrs}>")
        lines.append(f"      <title>{escape(_clip_text(item.title, MAX_TITLE_CHARS))}</title>")
        decision_text = (
            repair_common_mojibake(item.decision_text)
            if full_text
            else _clip_text(item.decision_text, MAX_TEXT_CHARS)
        )
        lines.append(f"      <text>{escape(decision_text)}</text>")
        if item.rationale:
            rationale = (
                repair_common_mojibake(item.rationale)
                if full_text
                else _clip_text(item.rationale, MAX_TEXT_CHARS)
            )
            lines.append(f"      <rationale>{escape(rationale)}</rationale>")
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
    return _render_research_agenda_with_links(
        agenda,
        experiment_links={},
        insight_links={},
        render_level="full",
    )


def _render_research_agenda_with_links(  # noqa: PLR0912
    agenda: ResearchAgenda | None,
    *,
    experiment_links: dict[str, list[CapabilityLink]],
    insight_links: dict[str, list[CapabilityLink]],
    render_level: str = "full",
    why_relevant: str = "",
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
    if why_relevant:
        lines.append(
            f"    <why_relevant>{escape(_clip_text(why_relevant, MAX_TEXT_CHARS))}</why_relevant>"
        )
    for experiment in agenda.experiments:
        attrs = (
            f"id={quoteattr(experiment.id)} "
            f"status={quoteattr(experiment.status.value)} "
            f"priority={quoteattr(f'{experiment.priority:.2f}')} "
            f"theory_id={quoteattr(experiment.theory_id or '')} "
            f"snapshot_id={quoteattr(experiment.snapshot_id or '')} "
            f"render_level={quoteattr(render_level)}"
        )
        lines.append(f"    <experiment {attrs}>")
        lines.append(
            f"      <title>{escape(_clip_text(experiment.title, MAX_TITLE_CHARS))}</title>"
        )
        if render_level == "stub":
            if experiment.source_episode_id:
                lines.append(f"      <source>{escape(experiment.source_episode_id)}</source>")
            lines.append("    </experiment>")
            continue
        lines.append(
            f"      <hypothesis>{escape(_clip_text(experiment.hypothesis, MAX_TEXT_CHARS if render_level == 'full' else MAX_LIST_ITEM_CHARS))}</hypothesis>"
        )
        if experiment.cohort_definition and render_level == "full":
            lines.append(
                f"      <cohort>{escape(_clip_text(experiment.cohort_definition, MAX_TEXT_CHARS))}</cohort>"
            )
        if experiment.success_criteria and render_level == "full":
            criteria = json.dumps(experiment.success_criteria, sort_keys=True)
            lines.append(
                f"      <success_criteria>{escape(_clip_text(criteria, MAX_COMMAND_CHARS))}</success_criteria>"
            )
        if experiment.command and render_level == "full":
            lines.append(
                f"      <command>{escape(_clip_text(experiment.command, MAX_COMMAND_CHARS))}</command>"
            )
        if render_level == "full":
            lines.extend(_render_capability_links(experiment_links.get(experiment.id, [])))
        lines.append("    </experiment>")

    for insight in agenda.insights:
        attrs = (
            f"id={quoteattr(insight.id)} "
            f"type={quoteattr(insight.insight_type.value)} "
            f"status={quoteattr(insight.status.value)} "
            f"confidence={quoteattr(f'{insight.confidence:.2f}')} "
            f"target_type={quoteattr(insight.target_type or '')} "
            f"target_id={quoteattr(insight.target_id or '')} "
            f"render_level={quoteattr(render_level)}"
        )
        lines.append(f"    <insight {attrs}>")
        lines.append(
            f"      <summary>{escape(_clip_text(insight.summary, MAX_TEXT_CHARS if render_level == 'full' else MAX_LIST_ITEM_CHARS))}</summary>"
        )
        if insight.proposed_action and render_level == "full":
            lines.append(
                f"      <proposed_action>{escape(_clip_text(insight.proposed_action, MAX_TEXT_CHARS))}</proposed_action>"
            )
        if render_level == "full":
            lines.extend(_render_capability_links(insight_links.get(insight.id, [])))
        lines.append("    </insight>")

    for concept in agenda.concepts:
        attrs = (
            f"id={quoteattr(concept.id)} "
            f"kind={quoteattr(concept.kind.value)} "
            f"confidence={quoteattr(f'{concept.confidence:.2f}')} "
            f"render_level={quoteattr(render_level)}"
        )
        lines.append(f"    <concept {attrs}>")
        lines.append(f"      <name>{escape(_clip_text(concept.name, MAX_TITLE_CHARS))}</name>")
        if render_level != "stub":
            lines.append(
                f"      <definition>{escape(_clip_text(concept.definition, MAX_TEXT_CHARS if render_level == 'full' else MAX_LIST_ITEM_CHARS))}</definition>"
            )
        lines.append("    </concept>")

    for snapshot in agenda.snapshots:
        attrs = (
            f"id={quoteattr(snapshot.id)} "
            f"key={quoteattr(snapshot.snapshot_key)} "
            f"source={quoteattr(snapshot.source)} "
            f"total_rows={quoteattr(str(snapshot.total_rows))} "
            f"render_level={quoteattr(render_level)}"
        )
        lines.append(f"    <snapshot {attrs}>")
        lines.append(f"      <title>{escape(_clip_text(snapshot.title, MAX_TITLE_CHARS))}</title>")
        if snapshot.duckdb_path and render_level == "full":
            lines.append(
                f"      <duckdb_path>{escape(_clip_text(snapshot.duckdb_path, MAX_COMMAND_CHARS))}</duckdb_path>"
            )
        lines.append("    </snapshot>")

    lines.append("  </research_agenda>")
    return lines


def _render_behavior_instruction(item: BehaviorInstruction) -> list[str]:
    attrs = (
        f"id={quoteattr(item.id)} "
        f"kind={quoteattr(item.kind.value)} "
        f"scope={quoteattr(item.scope.value)} "
        f"priority={quoteattr(item.priority.value)} "
        f"conflict_policy={quoteattr(item.conflict_policy.value)} "
        f"confidence={quoteattr(f'{item.confidence:.2f}')} "
        f"source={quoteattr(item.source_episode_id or '')} "
        f"source_type={quoteattr(item.source_type)} "
        f"source_id={quoteattr(item.source_id or '')}"
    )
    lines = [f"    <instruction {attrs}>"]
    lines.append(f"      <name>{escape(_clip_text(item.name, MAX_TITLE_CHARS))}</name>")
    lines.append(f"      <rule>{escape(_clip_text(item.rule, MAX_TEXT_CHARS))}</rule>")
    if item.rationale:
        lines.append(
            f"      <rationale>{escape(_clip_text(item.rationale, MAX_TEXT_CHARS))}</rationale>"
        )
    if item.reviewed_at or item.expires_at or item.conflict_group:
        governance_attrs = (
            f"reviewed_at={quoteattr(item.reviewed_at or '')} "
            f"expires_at={quoteattr(item.expires_at or '')} "
            f"conflict_group={quoteattr(item.conflict_group or '')} "
            f"application_count={quoteattr(str(item.application_count))}"
        )
        lines.append(f"      <governance {governance_attrs}/>")
    lines.extend(
        _render_string_items(
            container_tag="applies_to",
            item_tag="item",
            items=item.applies_to,
            indent="      ",
        )
    )
    lines.append("    </instruction>")
    return lines


def _render_behavior_instructions(items: BehaviorInstructionSet | None) -> list[str]:
    if items is None or not items.instructions:
        return ["  <behavior_instructions/>"]
    lines = ["  <behavior_instructions>"]
    for instruction in items.instructions:
        lines.extend(_render_behavior_instruction(instruction))
    lines.append("  </behavior_instructions>")
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


def _render_role(role: AgentRole, *, render_level: str) -> list[str]:
    attrs = (
        _capability_attrs(
            item_id=role.id,
            confidence=role.confidence,
            source_episode_id=role.source_episode_id,
        )
        + f" render_level={quoteattr(render_level)}"
    )
    lines = [f"    <role {attrs}>"]
    lines.append(f"      <name>{escape(_clip_text(role.name, MAX_TITLE_CHARS))}</name>")
    if render_level != "stub":
        lines.append(
            f"      <purpose>{escape(_clip_text(role.purpose, MAX_TEXT_CHARS if render_level == 'full' else MAX_LIST_ITEM_CHARS))}</purpose>"
        )
    if render_level == "full":
        lines.extend(
            _render_item_list(
                container_tag="responsibilities",
                item_tag="item",
                items=role.responsibilities,
            )
        )
    lines.append("    </role>")
    return lines


def _render_skill(skill: AgentSkill, *, render_level: str) -> list[str]:
    attrs = (
        _capability_attrs(
            item_id=skill.id,
            confidence=skill.confidence,
            source_episode_id=skill.source_episode_id,
        )
        + f" render_level={quoteattr(render_level)}"
    )
    lines = [f"    <skill {attrs}>"]
    lines.append(f"      <name>{escape(_clip_text(skill.name, MAX_TITLE_CHARS))}</name>")
    if render_level != "stub":
        lines.append(
            f"      <summary>{escape(_clip_text(skill.summary, MAX_TEXT_CHARS if render_level == 'full' else MAX_LIST_ITEM_CHARS))}</summary>"
        )
    if render_level == "full":
        lines.extend(
            _render_item_list(
                container_tag="when_to_use",
                item_tag="item",
                items=skill.when_to_use,
            )
        )
    lines.append("    </skill>")
    return lines


def _render_playbook(playbook: AgentPlaybook, *, render_level: str) -> list[str]:
    attrs = (
        _capability_attrs(
            item_id=playbook.id,
            confidence=playbook.confidence,
            source_episode_id=playbook.source_episode_id,
        )
        + f" render_level={quoteattr(render_level)}"
    )
    lines = [f"    <playbook {attrs}>"]
    lines.append(f"      <name>{escape(_clip_text(playbook.name, MAX_TITLE_CHARS))}</name>")
    if render_level != "stub":
        lines.append(
            f"      <goal>{escape(_clip_text(playbook.goal, MAX_TEXT_CHARS if render_level == 'full' else MAX_LIST_ITEM_CHARS))}</goal>"
        )
    if render_level == "full":
        lines.extend(
            _render_item_list(container_tag="steps", item_tag="step", items=playbook.steps)
        )
    lines.append("    </playbook>")
    return lines


def _render_agent_capabilities(
    capabilities: AgentCapabilities | None,
    *,
    render_level: str = "full",
    why_relevant: str = "",
) -> list[str]:
    if capabilities is None:
        return ["  <agent_capabilities/>"]
    if not capabilities.roles and not capabilities.skills and not capabilities.playbooks:
        return ["  <agent_capabilities/>"]

    lines = ["  <agent_capabilities>"]
    if why_relevant:
        lines.append(
            f"    <why_relevant>{escape(_clip_text(why_relevant, MAX_TEXT_CHARS))}</why_relevant>"
        )
    for role in capabilities.roles:
        lines.extend(_render_role(role, render_level=render_level))
    for skill in capabilities.skills:
        lines.extend(_render_skill(skill, render_level=render_level))
    for playbook in capabilities.playbooks:
        lines.extend(_render_playbook(playbook, render_level=render_level))
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


def _render(
    *,
    core: list[CoreMemory],
    task: TaskState | None,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    research_experiment_links: dict[str, list[CapabilityLink]],
    research_insight_links: dict[str, list[CapabilityLink]],
    behavior_instructions: BehaviorInstructionSet | None,
    agent_capabilities: AgentCapabilities | None,
    rules: list[ProceduralRule],
    facts: list[RetrievalCandidate],
    hits: list[ScoredHit],
    research_render_level: str = "full",
    capabilities_render_level: str = "full",
    context_omissions: list[dict[str, Any]] | None = None,
) -> str:
    lines = ["<memory_context>"]
    lines.extend(_render_core(core))
    lines.extend(_render_behavior_instructions(behavior_instructions))
    lines.extend(_render_task(task))
    lines.extend(_render_decisions(decisions))
    lines.extend(_render_theories(theories))
    lines.extend(
        _render_research_agenda_with_links(
            research_agenda,
            experiment_links=research_experiment_links,
            insight_links=research_insight_links,
            render_level=research_render_level,
            why_relevant="query intent reserved research agenda",
        )
    )
    lines.extend(
        _render_agent_capabilities(
            agent_capabilities,
            render_level=capabilities_render_level,
            why_relevant="query intent reserved agent capabilities",
        )
    )
    lines.extend(_render_rules(rules))
    lines.extend(_render_context_omissions(context_omissions or []))
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
    behavior_instructions: BehaviorInstructionSet | None,
    agent_capabilities: AgentCapabilities | None,
    rules: list[ProceduralRule],
    facts: list[RetrievalCandidate],
    research_render_level: str = "full",
    capabilities_render_level: str = "full",
    context_omissions: list[dict[str, Any]] | None = None,
) -> str:
    return _render(
        core=core,
        task=task,
        decisions=decisions,
        theories=theories,
        research_agenda=research_agenda,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        behavior_instructions=behavior_instructions,
        agent_capabilities=agent_capabilities,
        research_render_level=research_render_level,
        capabilities_render_level=capabilities_render_level,
        context_omissions=context_omissions,
        rules=rules,
        facts=facts,
        hits=[],
    )


def _structured_omissions(
    *,
    decisions: list[Decision],
    rendered_decisions: list[Decision],
    theories: list[TheoryContext],
    rendered_theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    rendered_research_agenda: ResearchAgenda | None,
    agent_capabilities: AgentCapabilities | None,
    rendered_capabilities: AgentCapabilities | None,
) -> list[dict[str, Any]]:
    omissions: list[dict[str, Any]] = []
    if research_agenda is not None and rendered_research_agenda is None:
        for experiment in research_agenda.experiments:
            omissions.append(
                {
                    "section": "research_agenda",
                    "type": "experiment",
                    "id": experiment.id,
                    "title": experiment.title,
                    "relevance": "elastic",
                }
            )
        for insight in research_agenda.insights:
            omissions.append(
                {
                    "section": "research_agenda",
                    "type": "insight",
                    "id": insight.id,
                    "title": insight.summary,
                    "relevance": "elastic",
                }
            )
        for concept in research_agenda.concepts:
            omissions.append(
                {
                    "section": "research_agenda",
                    "type": "concept",
                    "id": concept.id,
                    "title": concept.name,
                    "relevance": "elastic",
                }
            )
        for snapshot in research_agenda.snapshots:
            omissions.append(
                {
                    "section": "research_agenda",
                    "type": "snapshot",
                    "id": snapshot.id,
                    "title": snapshot.title,
                    "relevance": "elastic",
                }
            )
    if agent_capabilities is not None and rendered_capabilities is None:
        for role in agent_capabilities.roles:
            omissions.append(
                {
                    "section": "agent_capabilities",
                    "type": "role",
                    "id": role.id,
                    "title": role.name,
                    "relevance": "elastic",
                }
            )
        for skill in agent_capabilities.skills:
            omissions.append(
                {
                    "section": "agent_capabilities",
                    "type": "skill",
                    "id": skill.id,
                    "title": skill.name,
                    "relevance": "elastic",
                }
            )
        for playbook in agent_capabilities.playbooks:
            omissions.append(
                {
                    "section": "agent_capabilities",
                    "type": "playbook",
                    "id": playbook.id,
                    "title": playbook.name,
                    "relevance": "elastic",
                }
            )
    return omissions


def _structured_sections(
    *,
    decisions: list[Decision],
    rendered_decisions: list[Decision],
    theories: list[TheoryContext],
    rendered_theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    rendered_research_agenda: ResearchAgenda | None,
    research_render_level: str,
    agent_capabilities: AgentCapabilities | None,
    rendered_capabilities: AgentCapabilities | None,
    capabilities_render_level: str,
) -> list[dict[str, Any]]:
    return [
        _section_diag(
            name="active_decisions",
            render_level="full" if rendered_decisions else "none",
            included=len(rendered_decisions),
            omitted=max(0, len(decisions) - len(rendered_decisions)),
        ),
        _section_diag(
            name="active_theories",
            render_level="full" if rendered_theories else "none",
            included=len(rendered_theories),
            omitted=max(0, len(theories) - len(rendered_theories)),
        ),
        _section_diag(
            name="research_agenda",
            render_level=research_render_level if rendered_research_agenda is not None else "none",
            included=_agenda_count(rendered_research_agenda),
            omitted=max(
                0, _agenda_count(research_agenda) - _agenda_count(rendered_research_agenda)
            ),
        ),
        _section_diag(
            name="agent_capabilities",
            render_level=(
                capabilities_render_level if rendered_capabilities is not None else "none"
            ),
            included=_capabilities_count(rendered_capabilities),
            omitted=max(
                0,
                _capabilities_count(agent_capabilities)
                - _capabilities_count(rendered_capabilities),
            ),
        ),
    ]


def _fit_structured_sections(  # noqa: PLR0912
    *,
    max_tokens: int,
    chunk_reserve_tokens: int,
    intent: list[str],
    core: list[CoreMemory],
    task: TaskState | None,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    research_experiment_links: dict[str, list[CapabilityLink]],
    research_insight_links: dict[str, list[CapabilityLink]],
    behavior_instructions: BehaviorInstructionSet | None,
    agent_capabilities: AgentCapabilities | None,
    rules: list[ProceduralRule],
    facts: list[RetrievalCandidate],
) -> StructuredFit:
    protect_research = "research" in intent and _agenda_count(research_agenda) > 0
    protect_capabilities = "capability" in intent and _capabilities_count(agent_capabilities) > 0
    protect_decisions = "architecture" in intent and bool(decisions)
    protect_theories = "research" in intent and bool(theories)
    decision_variants = [decisions, decisions[:2], decisions[:1]]
    if not protect_decisions:
        decision_variants.append([])
    theory_variants = [theories, theories[:1]]
    if not protect_theories:
        theory_variants.append([])
    if _agenda_count(research_agenda) > 0:
        agenda_variants: list[tuple[ResearchAgenda | None, str]] = [
            (research_agenda, "full"),
            (research_agenda, "summary"),
            (research_agenda, "stub"),
        ]
    else:
        agenda_variants = [(None, "none")]
    if not protect_research and (None, "none") not in agenda_variants:
        agenda_variants.append((None, "none"))
    if _capabilities_count(agent_capabilities) > 0:
        capability_variants: list[tuple[AgentCapabilities | None, str]] = [
            (agent_capabilities, "full"),
            (agent_capabilities, "summary"),
            (agent_capabilities, "stub"),
        ]
    else:
        capability_variants = [(None, "none")]
    if not protect_capabilities and (None, "none") not in capability_variants:
        capability_variants.append((None, "none"))

    target_tokens = max(0, max_tokens - chunk_reserve_tokens)
    best = StructuredFit(
        decisions=decisions,
        theories=theories,
        research_agenda=research_agenda,
        agent_capabilities=agent_capabilities,
        research_render_level="full" if _agenda_count(research_agenda) else "none",
        capabilities_render_level="full" if _capabilities_count(agent_capabilities) else "none",
        sections=[],
        omissions=[],
        must_include=[],
    )
    best_tokens = estimate_tokens(
        _render_structured_only(
            core=core,
            task=task,
            decisions=decisions,
            theories=theories,
            research_agenda=research_agenda,
            research_experiment_links=research_experiment_links,
            research_insight_links=research_insight_links,
            behavior_instructions=behavior_instructions,
            agent_capabilities=agent_capabilities,
            rules=rules,
            facts=facts,
            research_render_level=best.research_render_level,
            capabilities_render_level=best.capabilities_render_level,
            context_omissions=[],
        )
    )
    best_score = -1
    must_include = [
        *([task.task_id] if task is not None else []),
        *([item.id for item in decisions] if protect_decisions else []),
        *([bundle.theory.id for bundle in theories] if protect_theories else []),
        *(_research_object_ids(research_agenda) if protect_research else []),
        *(_capability_object_ids(agent_capabilities) if protect_capabilities else []),
    ]

    for decision_items in decision_variants:
        for theory_items in theory_variants:
            for agenda, agenda_level in agenda_variants:
                for cap, cap_level in capability_variants:
                    omissions = _structured_omissions(
                        decisions=decisions,
                        rendered_decisions=decision_items,
                        theories=theories,
                        rendered_theories=theory_items,
                        research_agenda=research_agenda,
                        rendered_research_agenda=agenda,
                        agent_capabilities=agent_capabilities,
                        rendered_capabilities=cap,
                    )
                    text = _render_structured_only(
                        core=core,
                        task=task,
                        decisions=decision_items,
                        theories=theory_items,
                        research_agenda=agenda,
                        research_experiment_links=research_experiment_links,
                        research_insight_links=research_insight_links,
                        behavior_instructions=behavior_instructions,
                        agent_capabilities=cap,
                        rules=rules,
                        facts=facts,
                        research_render_level=agenda_level,
                        capabilities_render_level=cap_level,
                        context_omissions=omissions,
                    )
                    tokens = estimate_tokens(text)
                    score = (
                        len(decision_items) * 30
                        + len(theory_items) * 40
                        + _render_rank(agenda_level) * max(_agenda_count(agenda), 1) * 12
                        + _render_rank(cap_level) * max(_capabilities_count(cap), 1) * 8
                        - len(omissions) * 10
                    )
                    fit = StructuredFit(
                        decisions=decision_items,
                        theories=theory_items,
                        research_agenda=agenda,
                        agent_capabilities=cap,
                        research_render_level=agenda_level,
                        capabilities_render_level=cap_level,
                        sections=_structured_sections(
                            decisions=decisions,
                            rendered_decisions=decision_items,
                            theories=theories,
                            rendered_theories=theory_items,
                            research_agenda=research_agenda,
                            rendered_research_agenda=agenda,
                            research_render_level=agenda_level,
                            agent_capabilities=agent_capabilities,
                            rendered_capabilities=cap,
                            capabilities_render_level=cap_level,
                        ),
                        omissions=omissions,
                        must_include=must_include,
                    )
                    if tokens <= target_tokens and score > best_score:
                        best = fit
                        best_tokens = tokens
                        best_score = score
                    elif best_score < 0 and tokens < best_tokens:
                        best = fit
                        best_tokens = tokens
    if best_score < 0:
        best = StructuredFit(
            decisions=decisions[:1] if protect_decisions else [],
            theories=theories[:1] if protect_theories else [],
            research_agenda=research_agenda if protect_research else None,
            agent_capabilities=agent_capabilities if protect_capabilities else None,
            research_render_level="stub" if protect_research else "none",
            capabilities_render_level="stub" if protect_capabilities else "none",
            sections=_structured_sections(
                decisions=decisions,
                rendered_decisions=decisions[:1] if protect_decisions else [],
                theories=theories,
                rendered_theories=theories[:1] if protect_theories else [],
                research_agenda=research_agenda,
                rendered_research_agenda=research_agenda if protect_research else None,
                research_render_level="stub" if protect_research else "none",
                agent_capabilities=agent_capabilities,
                rendered_capabilities=agent_capabilities if protect_capabilities else None,
                capabilities_render_level="stub" if protect_capabilities else "none",
            ),
            omissions=[],
            must_include=must_include,
        )
    if not best.sections:
        best = StructuredFit(
            decisions=best.decisions,
            theories=best.theories,
            research_agenda=best.research_agenda,
            agent_capabilities=best.agent_capabilities,
            research_render_level=best.research_render_level,
            capabilities_render_level=best.capabilities_render_level,
            sections=_structured_sections(
                decisions=decisions,
                rendered_decisions=best.decisions,
                theories=theories,
                rendered_theories=best.theories,
                research_agenda=research_agenda,
                rendered_research_agenda=best.research_agenda,
                research_render_level=best.research_render_level,
                agent_capabilities=agent_capabilities,
                rendered_capabilities=best.agent_capabilities,
                capabilities_render_level=best.capabilities_render_level,
            ),
            omissions=_structured_omissions(
                decisions=decisions,
                rendered_decisions=best.decisions,
                theories=theories,
                rendered_theories=best.theories,
                research_agenda=research_agenda,
                rendered_research_agenda=best.research_agenda,
                agent_capabilities=agent_capabilities,
                rendered_capabilities=best.agent_capabilities,
            ),
            must_include=must_include,
        )
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
    feedback_scored = _apply_usage_feedback(conn, workspace_id=query.workspace_id, hits=scored)
    filtered = filter_active(feedback_scored, historical=query.historical)
    quality_filtered = filter_context_hits(filtered, historical=query.historical)
    clipped_hits = _clip_hits_for_context(quality_filtered)

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
    behavior_instructions = build_behavior_instruction_set(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        include_inactive=query.historical,
        limit=MAX_BEHAVIOR_INSTRUCTIONS,
    )
    agent_capabilities = build_agent_capabilities(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        include_inactive=query.historical,
        limit=MAX_AGENT_CAPABILITIES,
    )
    task = get_task_state(conn, query.workspace_id, query.task_id) if query.task_id else None

    intent = _detect_intent(query.query)
    structured_fit = _fit_structured_sections(
        max_tokens=query.max_tokens,
        chunk_reserve_tokens=_chunk_reserve_tokens(
            query.max_tokens,
            has_hits=bool(clipped_hits),
        ),
        intent=intent,
        core=core,
        task=task,
        decisions=decisions,
        theories=theories,
        research_agenda=research_agenda,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        behavior_instructions=behavior_instructions,
        agent_capabilities=agent_capabilities,
        rules=rules,
        facts=facts,
    )
    structured_text = _render_structured_only(
        core=core,
        task=task,
        decisions=structured_fit.decisions,
        theories=structured_fit.theories,
        research_agenda=structured_fit.research_agenda,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        behavior_instructions=behavior_instructions,
        agent_capabilities=structured_fit.agent_capabilities,
        research_render_level=structured_fit.research_render_level,
        capabilities_render_level=structured_fit.capabilities_render_level,
        context_omissions=structured_fit.omissions,
        rules=rules,
        facts=facts,
    )
    chunk_budget = max(
        0,
        query.max_tokens - estimate_tokens(structured_text) - STRUCTURED_SAFETY_RESERVE_TOKENS,
    )
    chunks_fit = fit_within_budget(clipped_hits, max_tokens=chunk_budget)
    chunks_fit = _ensure_exact_hit_stub(
        clipped_hits,
        chosen=chunks_fit,
        max_tokens=max(0, query.max_tokens - estimate_tokens(structured_text)),
    )

    text = _render(
        core=core,
        task=task,
        decisions=structured_fit.decisions,
        theories=structured_fit.theories,
        research_agenda=structured_fit.research_agenda,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        behavior_instructions=behavior_instructions,
        agent_capabilities=structured_fit.agent_capabilities,
        research_render_level=structured_fit.research_render_level,
        capabilities_render_level=structured_fit.capabilities_render_level,
        context_omissions=structured_fit.omissions,
        rules=rules,
        facts=facts,
        hits=chunks_fit,
    )
    budget_diagnostics = {
        "max_tokens": query.max_tokens,
        "estimated_tokens": estimate_tokens(text),
        "intent": intent,
        "sections": structured_fit.sections,
        "must_include": structured_fit.must_include,
        "omissions": structured_fit.omissions,
    }
    return BuiltContext(
        text=text,
        hits=chunks_fit,
        facts=facts,
        normalized=normalized,
        core=core,
        task_state=task,
        decisions=structured_fit.decisions,
        theories=structured_fit.theories,
        research_agenda=structured_fit.research_agenda,
        behavior_instructions=behavior_instructions,
        agent_capabilities=structured_fit.agent_capabilities,
        rules=rules,
        budget_diagnostics=budget_diagnostics,
    )
