"""SQL operations for ``domain_concepts``."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.enums import ConceptKind
from agent_memory_lite.models.research import DomainConcept
from agent_memory_lite.repositories.research_helpers import (
    contains_all,
    row_to_concept,
    tokens_from,
)
from agent_memory_lite.utils.sql_filters import date_range_clause


def upsert_concept_row(
    conn: sqlite3.Connection,
    *,
    concept_id: str,
    workspace_id: str,
    name: str,
    kind: ConceptKind,
    definition: str,
    aliases: list[str],
    tags: list[str],
    source_episode_id: str | None,
    confidence: float,
    active: bool,
    created_at: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO domain_concepts (
            id, workspace_id, name, kind, definition, aliases_json, tags_json,
            source_episode_id, confidence, active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, name) DO UPDATE SET
            kind = excluded.kind,
            definition = excluded.definition,
            aliases_json = excluded.aliases_json,
            tags_json = excluded.tags_json,
            source_episode_id = excluded.source_episode_id,
            confidence = excluded.confidence,
            active = excluded.active,
            updated_at = excluded.updated_at
        """,
        (
            concept_id,
            workspace_id,
            name,
            kind.value,
            definition,
            json.dumps(aliases, sort_keys=True),
            json.dumps(tags, sort_keys=True),
            source_episode_id,
            confidence,
            1 if active else 0,
            created_at,
            updated_at,
        ),
    )


def get_concept_by_name(
    conn: sqlite3.Connection, *, workspace_id: str, name: str
) -> DomainConcept | None:
    row = conn.execute(
        "SELECT * FROM domain_concepts WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return row_to_concept(row) if row is not None else None


def get_concept_by_id(conn: sqlite3.Connection, concept_id: str) -> DomainConcept | None:
    row = conn.execute("SELECT * FROM domain_concepts WHERE id = ?", (concept_id,)).fetchone()
    return row_to_concept(row) if row is not None else None


def _concept_text(concept: DomainConcept) -> str:
    return " ".join(
        [concept.name, concept.definition, " ".join(concept.aliases), " ".join(concept.tags)]
    )


def list_concepts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
    since: str | None = None,
    until: str | None = None,
) -> list[DomainConcept]:
    extra_sql, extra_params = date_range_clause(since=since, until=until)
    rows = conn.execute(
        f"SELECT * FROM domain_concepts WHERE workspace_id = ? {extra_sql}",
        (workspace_id, *extra_params),
    ).fetchall()
    terms = tokens_from(query)
    concepts = [row_to_concept(row) for row in rows]
    if not include_inactive:
        concepts = [item for item in concepts if item.active]
    concepts = [item for item in concepts if contains_all(_concept_text(item), terms)]
    concepts.sort(key=lambda item: (item.confidence, item.updated_at), reverse=True)
    return concepts[:limit]
