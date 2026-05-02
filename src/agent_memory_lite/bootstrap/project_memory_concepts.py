"""Vocabulary concepts written by the neutral project-memory seed.

Split out of ``project_memory_seed_templates.py`` so each template file
stays under the SLOC ceiling.
"""

from __future__ import annotations

from agent_memory_lite.models.enums import ConceptKind
from agent_memory_lite.models.research import DomainConceptIn


def vocabulary_concepts(workspace_id: str, source_episode_id: str | None) -> list[DomainConceptIn]:
    return [
        DomainConceptIn(
            workspace_id=workspace_id,
            name="workspace_id",
            kind=ConceptKind.TERM,
            definition=(
                "Logical namespace inside a memory database. In project mode, use the "
                "project's established workspace id and do not silently write durable "
                "rows to default."
            ),
            aliases=["memory namespace", "workspace namespace"],
            tags=["memory-bootstrap", "workspace-isolation"],
            source_episode_id=source_episode_id,
            confidence=0.9,
        ),
        DomainConceptIn(
            workspace_id=workspace_id,
            name="memory candidate review",
            kind=ConceptKind.TERM,
            definition=(
                "Review-first workflow where extracted candidates are promoted only when "
                "evidence supports them and rejected when weak or wrong."
            ),
            aliases=["candidate triage", "candidate promote reject"],
            tags=["memory-bootstrap", "review-queue"],
            source_episode_id=source_episode_id,
            confidence=0.9,
        ),
        DomainConceptIn(
            workspace_id=workspace_id,
            name="memory snapshot",
            kind=ConceptKind.ARTIFACT,
            definition=(
                "Immutable reference to a database export, replay dataset, or research "
                "artifact with paths and table counts so later experiments are repeatable."
            ),
            aliases=["dataset snapshot", "research snapshot"],
            tags=["memory-bootstrap", "research"],
            source_episode_id=source_episode_id,
            confidence=0.9,
        ),
        DomainConceptIn(
            workspace_id=workspace_id,
            name="memory integrity audit",
            kind=ConceptKind.TERM,
            definition=(
                "Read-only proof that SQLite, FTS, vector rows, workspace isolation, "
                "hygiene, and MCP retrieval are consistent enough to trust."
            ),
            aliases=["memory audit", "retrieval integrity"],
            tags=["memory-bootstrap", "maintenance"],
            source_episode_id=source_episode_id,
            confidence=0.9,
        ),
    ]
