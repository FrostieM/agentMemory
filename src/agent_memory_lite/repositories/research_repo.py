"""SQL operations for research-lab memory objects."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from agent_memory_lite.models.enums import (
    ConceptKind,
    ExperimentStatus,
    InsightStatus,
    InsightType,
    TheoryEvidenceKind,
)
from agent_memory_lite.models.research import (
    DomainConcept,
    Experiment,
    ExperimentResult,
    MemorySnapshot,
    ResearchAgenda,
    ResearchInsight,
)

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)
_OPEN_EXPERIMENT_STATUSES = {
    ExperimentStatus.PLANNED.value,
    ExperimentStatus.RUNNING.value,
    ExperimentStatus.BLOCKED.value,
}
_OPEN_INSIGHT_STATUSES = {InsightStatus.NEW.value, InsightStatus.ACCEPTED.value}


def _json_list(raw: str | None) -> list[str]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _json_dict(raw: str | None) -> dict[str, Any]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def _json_int_dict(raw: str | None) -> dict[str, int]:
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in data.items():
        if isinstance(value, (bool, int, float)):
            result[str(key)] = int(value)
    return result


def _tokens(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def _contains_all(text: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    lower = text.lower()
    return any(token in lower for token in tokens)


def _row_to_snapshot(row: sqlite3.Row) -> MemorySnapshot:
    return MemorySnapshot(
        id=row["id"],
        workspace_id=row["workspace_id"],
        snapshot_key=row["snapshot_key"],
        title=row["title"],
        source=row["source_label"],
        db_path=row["db_path"],
        duckdb_path=row["duckdb_path"],
        parquet_dir=row["parquet_dir"],
        window_start=row["window_start"],
        window_end=row["window_end"],
        build_sha=row["build_sha"],
        build_branch=row["build_branch"],
        build_time=row["build_time"],
        remote_host=row["remote_host"],
        table_counts=_json_int_dict(row["table_counts_json"]),
        total_rows=int(row["total_rows"]),
        metadata=_json_dict(row["metadata_json"]),
        source_episode_id=row["source_episode_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_experiment(row: sqlite3.Row) -> Experiment:
    return Experiment(
        id=row["id"],
        workspace_id=row["workspace_id"],
        theory_id=row["theory_id"],
        snapshot_id=row["snapshot_id"],
        title=row["title"],
        hypothesis=row["hypothesis"],
        cohort_definition=row["cohort_definition"],
        success_criteria=_json_dict(row["success_criteria_json"]),
        command=row["command"],
        status=ExperimentStatus(row["status"]),
        priority=float(row["priority"]),
        owner=row["owner"],
        due_at=row["due_at"],
        source_episode_id=row["source_episode_id"],
        metadata=_json_dict(row["metadata_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _row_to_result(row: sqlite3.Row) -> ExperimentResult:
    return ExperimentResult(
        id=row["id"],
        workspace_id=row["workspace_id"],
        experiment_id=row["experiment_id"],
        theory_id=row["theory_id"],
        kind=TheoryEvidenceKind(row["kind"]),
        summary=row["summary"],
        metrics=_json_dict(row["metrics_json"]),
        artifact_path=row["artifact_path"],
        confidence=float(row["confidence"]),
        observed_at=row["observed_at"],
        source_episode_id=row["source_episode_id"],
        created_at=row["created_at"],
    )


def _row_to_concept(row: sqlite3.Row) -> DomainConcept:
    return DomainConcept(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        kind=ConceptKind(row["kind"]),
        definition=row["definition"],
        aliases=_json_list(row["aliases_json"]),
        tags=_json_list(row["tags_json"]),
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_insight(row: sqlite3.Row) -> ResearchInsight:
    return ResearchInsight(
        id=row["id"],
        workspace_id=row["workspace_id"],
        insight_type=InsightType(row["insight_type"]),
        summary=row["summary"],
        proposed_action=row["proposed_action"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        source_episode_ids=_json_list(row["source_episode_ids_json"]),
        confidence=float(row["confidence"]),
        status=InsightStatus(row["status"]),
        tags=_json_list(row["tags_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def upsert_snapshot_row(
    conn: sqlite3.Connection,
    *,
    snapshot_id: str,
    workspace_id: str,
    snapshot_key: str,
    title: str,
    source: str,
    db_path: str | None,
    duckdb_path: str | None,
    parquet_dir: str | None,
    window_start: str | None,
    window_end: str | None,
    build_sha: str | None,
    build_branch: str | None,
    build_time: str | None,
    remote_host: str | None,
    table_counts: dict[str, int],
    total_rows: int,
    metadata: dict[str, Any],
    source_episode_id: str | None,
    created_at: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO memory_snapshots (
            id, workspace_id, snapshot_key, title, source_label, db_path,
            duckdb_path, parquet_dir, window_start, window_end, build_sha,
            build_branch, build_time, remote_host, table_counts_json, total_rows,
            metadata_json, source_episode_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, snapshot_key) DO UPDATE SET
            title = excluded.title,
            source_label = excluded.source_label,
            db_path = excluded.db_path,
            duckdb_path = excluded.duckdb_path,
            parquet_dir = excluded.parquet_dir,
            window_start = excluded.window_start,
            window_end = excluded.window_end,
            build_sha = excluded.build_sha,
            build_branch = excluded.build_branch,
            build_time = excluded.build_time,
            remote_host = excluded.remote_host,
            table_counts_json = excluded.table_counts_json,
            total_rows = excluded.total_rows,
            metadata_json = excluded.metadata_json,
            source_episode_id = excluded.source_episode_id,
            updated_at = excluded.updated_at
        """,
        (
            snapshot_id,
            workspace_id,
            snapshot_key,
            title,
            source,
            db_path,
            duckdb_path,
            parquet_dir,
            window_start,
            window_end,
            build_sha,
            build_branch,
            build_time,
            remote_host,
            json.dumps(table_counts, sort_keys=True),
            total_rows,
            json.dumps(metadata, sort_keys=True),
            source_episode_id,
            created_at,
            updated_at,
        ),
    )


def get_snapshot(conn: sqlite3.Connection, snapshot_id: str) -> MemorySnapshot | None:
    row = conn.execute("SELECT * FROM memory_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return _row_to_snapshot(row) if row is not None else None


def get_snapshot_by_key(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    snapshot_key: str,
) -> MemorySnapshot | None:
    row = conn.execute(
        "SELECT * FROM memory_snapshots WHERE workspace_id = ? AND snapshot_key = ?",
        (workspace_id, snapshot_key),
    ).fetchone()
    return _row_to_snapshot(row) if row is not None else None


def _snapshot_text(snapshot: MemorySnapshot) -> str:
    return " ".join(
        [
            snapshot.snapshot_key,
            snapshot.title,
            snapshot.source,
            snapshot.db_path or "",
            snapshot.duckdb_path or "",
            snapshot.parquet_dir or "",
            snapshot.remote_host or "",
            " ".join(snapshot.table_counts),
        ]
    )


def list_snapshots(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    limit: int = 10,
) -> list[MemorySnapshot]:
    rows = conn.execute(
        """
        SELECT * FROM memory_snapshots
        WHERE workspace_id = ?
        ORDER BY updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    tokens = _tokens(query)
    snapshots = [_row_to_snapshot(row) for row in rows]
    snapshots = [item for item in snapshots if _contains_all(_snapshot_text(item), tokens)]
    return snapshots[:limit]


def insert_experiment_row(
    conn: sqlite3.Connection,
    *,
    experiment_id: str,
    workspace_id: str,
    theory_id: str | None,
    snapshot_id: str | None,
    title: str,
    hypothesis: str,
    cohort_definition: str | None,
    success_criteria: dict[str, Any],
    command: str | None,
    status: ExperimentStatus,
    priority: float,
    owner: str | None,
    due_at: str | None,
    source_episode_id: str | None,
    metadata: dict[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO research_experiments (
            id, workspace_id, theory_id, snapshot_id, title, hypothesis,
            cohort_definition, success_criteria_json, command, status, priority,
            owner, due_at, source_episode_id, metadata_json, created_at,
            updated_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            experiment_id,
            workspace_id,
            theory_id,
            snapshot_id,
            title,
            hypothesis,
            cohort_definition,
            json.dumps(success_criteria, sort_keys=True),
            command,
            status.value,
            priority,
            owner,
            due_at,
            source_episode_id,
            json.dumps(metadata, sort_keys=True),
            created_at,
            created_at,
        ),
    )


def get_experiment(conn: sqlite3.Connection, experiment_id: str) -> Experiment | None:
    row = conn.execute(
        "SELECT * FROM research_experiments WHERE id = ?", (experiment_id,)
    ).fetchone()
    return _row_to_experiment(row) if row is not None else None


def _experiment_text(experiment: Experiment) -> str:
    return " ".join(
        [
            experiment.title,
            experiment.hypothesis,
            experiment.cohort_definition or "",
            experiment.command or "",
            " ".join(str(key) for key in experiment.success_criteria),
        ]
    )


def _rank_experiment(experiment: Experiment, tokens: list[str]) -> tuple[float, str]:
    status_bonus = {
        ExperimentStatus.RUNNING: 0.35,
        ExperimentStatus.PLANNED: 0.25,
        ExperimentStatus.BLOCKED: 0.05,
        ExperimentStatus.COMPLETED: -0.20,
        ExperimentStatus.CANCELLED: -0.35,
    }[experiment.status]
    text = _experiment_text(experiment).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    return token_score + experiment.priority + status_bonus, experiment.updated_at


def list_experiments(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    statuses: list[ExperimentStatus] | None = None,
    limit: int = 20,
) -> list[Experiment]:
    rows = conn.execute(
        "SELECT * FROM research_experiments WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    experiments = [_row_to_experiment(row) for row in rows]
    if statuses is not None:
        allowed = {status.value for status in statuses}
        experiments = [item for item in experiments if item.status.value in allowed]
    terms = _tokens(query)
    experiments = [item for item in experiments if _contains_all(_experiment_text(item), terms)]
    experiments.sort(key=lambda item: _rank_experiment(item, terms), reverse=True)
    return experiments[:limit]


def mark_experiment_completed(
    conn: sqlite3.Connection,
    *,
    experiment_id: str,
    updated_at: str,
    completed_at: str,
) -> None:
    conn.execute(
        """
        UPDATE research_experiments
        SET status = 'completed', updated_at = ?, completed_at = COALESCE(completed_at, ?)
        WHERE id = ?
        """,
        (updated_at, completed_at, experiment_id),
    )


def insert_experiment_result_row(
    conn: sqlite3.Connection,
    *,
    result_id: str,
    workspace_id: str,
    experiment_id: str,
    theory_id: str | None,
    kind: TheoryEvidenceKind,
    summary: str,
    metrics: dict[str, Any],
    artifact_path: str | None,
    confidence: float,
    observed_at: str,
    source_episode_id: str | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO experiment_results (
            id, workspace_id, experiment_id, theory_id, kind, summary,
            metrics_json, artifact_path, confidence, observed_at,
            source_episode_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result_id,
            workspace_id,
            experiment_id,
            theory_id,
            kind.value,
            summary,
            json.dumps(metrics, sort_keys=True),
            artifact_path,
            confidence,
            observed_at,
            source_episode_id,
            created_at,
        ),
    )


def get_experiment_result(
    conn: sqlite3.Connection,
    result_id: str,
) -> ExperimentResult | None:
    row = conn.execute("SELECT * FROM experiment_results WHERE id = ?", (result_id,)).fetchone()
    return _row_to_result(row) if row is not None else None


def list_experiment_results(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    experiment_id: str | None = None,
    theory_id: str | None = None,
    limit: int = 20,
) -> list[ExperimentResult]:
    clauses = ["workspace_id = ?"]
    params: list[str | int] = [workspace_id]
    if experiment_id is not None:
        clauses.append("experiment_id = ?")
        params.append(experiment_id)
    if theory_id is not None:
        clauses.append("theory_id = ?")
        params.append(theory_id)
    rows = conn.execute(
        f"""
        SELECT * FROM experiment_results
        WHERE {" AND ".join(clauses)}
        ORDER BY observed_at DESC, created_at DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [_row_to_result(row) for row in rows]


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
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    name: str,
) -> DomainConcept | None:
    row = conn.execute(
        "SELECT * FROM domain_concepts WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_concept(row) if row is not None else None


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
) -> list[DomainConcept]:
    rows = conn.execute(
        "SELECT * FROM domain_concepts WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    terms = _tokens(query)
    concepts = [_row_to_concept(row) for row in rows]
    if not include_inactive:
        concepts = [item for item in concepts if item.active]
    concepts = [item for item in concepts if _contains_all(_concept_text(item), terms)]
    concepts.sort(key=lambda item: (item.confidence, item.updated_at), reverse=True)
    return concepts[:limit]


def insert_insight_row(
    conn: sqlite3.Connection,
    *,
    insight_id: str,
    workspace_id: str,
    insight_type: InsightType,
    summary: str,
    proposed_action: str | None,
    target_type: str | None,
    target_id: str | None,
    source_episode_ids: list[str],
    confidence: float,
    status: InsightStatus,
    tags: list[str],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO research_insights (
            id, workspace_id, insight_type, summary, proposed_action,
            target_type, target_id, source_episode_ids_json, confidence,
            status, tags_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            insight_id,
            workspace_id,
            insight_type.value,
            summary,
            proposed_action,
            target_type,
            target_id,
            json.dumps(source_episode_ids, sort_keys=True),
            confidence,
            status.value,
            json.dumps(tags, sort_keys=True),
            created_at,
            created_at,
        ),
    )


def get_insight(conn: sqlite3.Connection, insight_id: str) -> ResearchInsight | None:
    row = conn.execute("SELECT * FROM research_insights WHERE id = ?", (insight_id,)).fetchone()
    return _row_to_insight(row) if row is not None else None


def _insight_text(insight: ResearchInsight) -> str:
    return " ".join(
        [
            insight.summary,
            insight.proposed_action or "",
            insight.target_type or "",
            insight.target_id or "",
            " ".join(insight.tags),
        ]
    )


def _rank_insight(insight: ResearchInsight, tokens: list[str]) -> tuple[float, str]:
    status_bonus = {
        InsightStatus.NEW: 0.30,
        InsightStatus.ACCEPTED: 0.18,
        InsightStatus.REJECTED: -0.25,
        InsightStatus.ARCHIVED: -0.35,
    }[insight.status]
    text = _insight_text(insight).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    return token_score + insight.confidence + status_bonus, insight.updated_at


def list_insights(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    statuses: list[InsightStatus] | None = None,
    limit: int = 20,
) -> list[ResearchInsight]:
    rows = conn.execute(
        "SELECT * FROM research_insights WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    insights = [_row_to_insight(row) for row in rows]
    if statuses is not None:
        allowed = {status.value for status in statuses}
        insights = [item for item in insights if item.status.value in allowed]
    terms = _tokens(query)
    insights = [item for item in insights if _contains_all(_insight_text(item), terms)]
    insights.sort(key=lambda item: _rank_insight(item, terms), reverse=True)
    return insights[:limit]


def build_research_agenda(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    limit: int = 10,
) -> ResearchAgenda:
    return ResearchAgenda(
        snapshots=list_snapshots(
            conn, workspace_id=workspace_id, query=query, limit=max(1, limit // 3)
        ),
        experiments=list_experiments(
            conn,
            workspace_id=workspace_id,
            query=query,
            statuses=[ExperimentStatus.PLANNED, ExperimentStatus.RUNNING, ExperimentStatus.BLOCKED],
            limit=limit,
        ),
        insights=list_insights(
            conn,
            workspace_id=workspace_id,
            query=query,
            statuses=[InsightStatus.NEW, InsightStatus.ACCEPTED],
            limit=limit,
        ),
        concepts=list_concepts(
            conn, workspace_id=workspace_id, query=query, limit=max(1, limit // 2)
        ),
    )
