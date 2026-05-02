"""Dataclasses returned by the context builder.

Kept in a tiny module so the rendering and fitting code can import the
shape without dragging the whole pipeline in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.models.behavior import BehaviorInstructionSet
from agent_memory_lite.models.capabilities import AgentCapabilities
from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.core_memory import CoreMemory
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.procedural import ProceduralRule
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.models.retrieval import RetrievalCandidate, ScoredHit
from agent_memory_lite.models.task_state import TaskState
from agent_memory_lite.models.theories import Theory, TheoryEvidence
from agent_memory_lite.retrieval.normalize import NormalizedQuery


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
