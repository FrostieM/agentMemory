"""Local browser UI for inspecting memory as a live graph."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, StreamingResponse

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_allowed
from agent_memory_lite.api.ui_telemetry import event_stream, ui_telemetry
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry
from agent_memory_lite.models.enums import MaintenanceEventStatus
from agent_memory_lite.repositories.maintenance_repo import list_maintenance_events
from agent_memory_lite.utils.time import iso_now

router = APIRouter(include_in_schema=False)

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_UI_ROOT = _PACKAGE_ROOT / "ui"
_ASSETS = {
    "app.js": "application/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
}

_GROUPS: dict[str, tuple[str, str]] = {
    "episodic": ("Episodic log", "What happened"),
    "retrieval": ("Retrieval", "Searchable chunks and files"),
    "research": ("Research lab", "Theories, evidence, experiments"),
    "capability": ("Capabilities", "Roles, skills, playbooks"),
    "governance": ("Governance", "Decisions and instructions"),
    "operations": ("Operations", "Tasks, candidates, maintenance"),
    "feedback": ("Feedback", "User ranking signal"),
}

_TABLES: list[dict[str, str]] = [
    {"table": "episodes", "label": "Episodes", "group": "episodic", "text": "raw_text"},
    {"table": "chunks", "label": "Chunks", "group": "retrieval", "text": "text"},
    {"table": "files", "label": "Files", "group": "retrieval", "text": "path"},
    {"table": "decisions", "label": "Decisions", "group": "governance", "text": "title"},
    {"table": "behavior_instructions", "label": "Behavior", "group": "governance", "text": "name"},
    {"table": "theories", "label": "Theories", "group": "research", "text": "title"},
    {"table": "theory_evidence", "label": "Evidence", "group": "research", "text": "summary"},
    {
        "table": "research_experiments",
        "label": "Experiments",
        "group": "research",
        "text": "title",
    },
    {"table": "experiment_results", "label": "Results", "group": "research", "text": "summary"},
    {"table": "memory_snapshots", "label": "Snapshots", "group": "research", "text": "title"},
    {"table": "research_insights", "label": "Insights", "group": "research", "text": "summary"},
    {"table": "domain_concepts", "label": "Concepts", "group": "research", "text": "name"},
    {"table": "agent_roles", "label": "Roles", "group": "capability", "text": "name"},
    {"table": "agent_skills", "label": "Skills", "group": "capability", "text": "name"},
    {"table": "agent_playbooks", "label": "Playbooks", "group": "capability", "text": "name"},
    {
        "table": "capability_links",
        "label": "Capability links",
        "group": "capability",
        "text": "relation",
    },
    {"table": "task_state", "label": "Tasks", "group": "operations", "text": "goal"},
    {
        "table": "memory_candidates",
        "label": "Candidates",
        "group": "operations",
        "text": "evidence",
    },
    {
        "table": "maintenance_events",
        "label": "Maintenance",
        "group": "operations",
        "text": "summary",
    },
    {
        "table": "memory_usage_feedback",
        "label": "Usage feedback",
        "group": "feedback",
        "text": "notes",
    },
]

_TIME_COLUMNS = ("updated_at", "created_at", "observed_at", "valid_from", "last_indexed_at")

_PROCESS_STAGES: list[dict[str, Any]] = [
    {
        "id": "capture",
        "label": "Capture",
        "verb": "records raw events",
        "tables": ["episodes", "ingested_files"],
    },
    {
        "id": "index",
        "label": "Index",
        "verb": "chunks and embeds content",
        "tables": ["chunks", "files"],
    },
    {
        "id": "retrieve",
        "label": "Retrieve",
        "verb": "finds exact and semantic matches",
        "tables": ["chunks", "memory_usage_feedback"],
    },
    {
        "id": "context",
        "label": "Context",
        "verb": "builds the agent envelope",
        "tables": ["behavior_instructions", "decisions", "task_state"],
    },
    {
        "id": "research",
        "label": "Research",
        "verb": "tracks hypotheses and evidence",
        "tables": [
            "theories",
            "theory_evidence",
            "research_experiments",
            "experiment_results",
            "memory_snapshots",
            "research_insights",
            "domain_concepts",
        ],
    },
    {
        "id": "capabilities",
        "label": "Capabilities",
        "verb": "links roles, skills, playbooks",
        "tables": ["agent_roles", "agent_skills", "agent_playbooks", "capability_links"],
    },
    {
        "id": "governance",
        "label": "Govern",
        "verb": "keeps trust work visible",
        "tables": ["memory_candidates", "maintenance_events"],
    },
]

_TABLE_TO_STAGE = {table: stage["id"] for stage in _PROCESS_STAGES for table in stage["tables"]}
_PROCESS_EDGES = [
    {"source": "capture", "target": "index", "label": "chunk"},
    {"source": "index", "target": "retrieve", "label": "search"},
    {"source": "retrieve", "target": "context", "label": "rank"},
    {"source": "context", "target": "research", "label": "reason"},
    {"source": "research", "target": "capabilities", "label": "shape"},
    {"source": "capabilities", "target": "governance", "label": "verify"},
]


@router.get("/ui")
def memory_ui_index() -> FileResponse:
    return FileResponse(_UI_ROOT / "index.html", media_type="text/html; charset=utf-8")


@router.get("/ui/{asset_name}")
def memory_ui_asset(asset_name: str) -> FileResponse:
    if asset_name not in _ASSETS:
        return FileResponse(_UI_ROOT / "index.html", media_type="text/html; charset=utf-8")
    return FileResponse(_UI_ROOT / asset_name, media_type=_ASSETS[asset_name])


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'virtual table') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({_quote_ident(table)})")}


def _count(conn: sqlite3.Connection, table: str, *, workspace_id: str) -> int:
    columns = _columns(conn, table)
    if not columns:
        return 0
    if "workspace_id" in columns:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {_quote_ident(table)} WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    else:
        row = conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}").fetchone()
    return int(row[0] or 0)


def _available_workspaces(conn: sqlite3.Connection, settings: Settings) -> list[str]:
    if settings.strict_workspace_isolation and not settings.hub_mode:
        return [settings.workspace_id]
    workspaces = {settings.workspace_id}
    for spec in _TABLES:
        table = spec["table"]
        columns = _columns(conn, table)
        if "workspace_id" not in columns:
            continue
        rows = conn.execute(
            f"""
            SELECT DISTINCT workspace_id
            FROM {_quote_ident(table)}
            WHERE workspace_id IS NOT NULL AND workspace_id != ''
            ORDER BY workspace_id
            LIMIT 50
            """
        ).fetchall()
        workspaces.update(str(row[0]) for row in rows if row[0])
    if settings.hub_mode:
        for entry in WorkspaceRegistry(settings.workspaces_file).list():
            workspaces.add(entry.id)
    if settings.forbid_default_workspace:
        workspaces.discard("default")
    return sorted(workspaces)


def _registered_workspaces(settings: Settings, current_workspace_id: str) -> list[dict[str, Any]]:
    """Hub-mode registry view: every workspace with its physical DB paths."""
    registry = WorkspaceRegistry(settings.workspaces_file)
    seen: dict[str, dict[str, Any]] = {}
    for entry in registry.list():
        seen[entry.id] = {
            "id": entry.id,
            "label": entry.label or entry.id,
            "db_path": entry.db_path,
            "vector_path": entry.vector_path,
            "project_root": entry.project_root,
            "registered_at": entry.registered_at,
            "last_seen_at": entry.last_seen_at,
            "is_current": entry.id == current_workspace_id,
        }
    if settings.workspace_id and settings.workspace_id not in seen:
        seen[settings.workspace_id] = {
            "id": settings.workspace_id,
            "label": settings.workspace_id,
            "db_path": str(settings.db_path),
            "vector_path": str(settings.vector_db_path),
            "project_root": "",
            "registered_at": "",
            "last_seen_at": "",
            "is_current": settings.workspace_id == current_workspace_id,
        }
    return sorted(seen.values(), key=lambda item: str(item["id"]))


def _time_column(columns: set[str]) -> str | None:
    return next((column for column in _TIME_COLUMNS if column in columns), None)


def _latest_rows(
    conn: sqlite3.Connection,
    table: str,
    *,
    workspace_id: str,
    limit: int,
) -> list[sqlite3.Row]:
    columns = _columns(conn, table)
    if not columns:
        return []
    time_column = _time_column(columns)
    order = _quote_ident(time_column) if time_column else "rowid"
    where = "WHERE workspace_id = ?" if "workspace_id" in columns else ""
    params: tuple[Any, ...] = (workspace_id, limit) if where else (limit,)
    return list(
        conn.execute(
            f"""
            SELECT *
            FROM {_quote_ident(table)}
            {where}
            ORDER BY {order} DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    )


def _clip(value: object, limit: int = 96) -> str:
    text = "" if value is None else str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _row_id(row: sqlite3.Row) -> str:
    keys = set(row.keys())
    for key in ("id", "chunk_id", "task_id", "source_id"):
        if key in keys and row[key] is not None:
            return str(row[key])
    return hashlib.blake2s(repr(dict(row)).encode("utf-8"), digest_size=8).hexdigest()


def _row_time(row: sqlite3.Row) -> str | None:
    keys = set(row.keys())
    for key in _TIME_COLUMNS:
        if key in keys and row[key]:
            return str(row[key])
    return None


def _status(row: sqlite3.Row) -> str | None:
    keys = set(row.keys())
    for key in ("status", "kind", "source_type", "severity"):
        if key in keys and row[key]:
            return str(row[key])
    return None


def _add_node(
    nodes: dict[str, dict[str, Any]],
    *,
    node_id: str,
    label: str,
    kind: str,
    group: str,
    count: int | None = None,
    status: str | None = None,
    updated_at: str | None = None,
    detail: str | None = None,
) -> None:
    nodes.setdefault(
        node_id,
        {
            "id": node_id,
            "label": label,
            "kind": kind,
            "group": group,
            "count": count,
            "status": status,
            "updated_at": updated_at,
            "detail": detail,
        },
    )


def _add_edge(
    edges: dict[str, dict[str, Any]],
    *,
    source: str,
    target: str,
    label: str,
    kind: str,
) -> None:
    edge_id = f"{source}->{target}:{label}"
    edges.setdefault(
        edge_id,
        {"id": edge_id, "source": source, "target": target, "label": label, "kind": kind},
    )


def _table_node_id(table: str) -> str:
    return f"table:{table}"


def _item_node_id(table: str, row_id: str) -> str:
    return f"{table}:{row_id}"


def _add_reference(
    nodes: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
    *,
    table: str,
    item_id: str,
    row: sqlite3.Row,
) -> None:
    keys = set(row.keys())
    source_type_tables = {
        "chunk": "chunks",
        "decision": "decisions",
        "theory": "theories",
        "insight": "research_insights",
    }
    source_table = (
        source_type_tables.get(str(row["source_type"]))
        if "source_type" in keys and row["source_type"]
        else ""
    )
    references = [
        ("source_episode_id", "episodes", "source"),
        ("episode_id", "episodes", "episode"),
        ("theory_id", "theories", "theory"),
        ("snapshot_id", "memory_snapshots", "snapshot"),
        ("experiment_id", "research_experiments", "experiment"),
        ("source_id", source_table or "", "rates"),
        (
            "target_id",
            str(row["target_type"]) if "target_type" in keys and row["target_type"] else "",
            "target",
        ),
        (
            "capability_id",
            str(row["capability_type"])
            if "capability_type" in keys and row["capability_type"]
            else "",
            "capability",
        ),
    ]
    for column, ref_table, label in references:
        if column not in keys or not row[column] or not ref_table:
            continue
        ref_id = str(row[column])
        ref_node = _item_node_id(ref_table, ref_id)
        _add_node(
            nodes,
            node_id=ref_node,
            label=_clip(ref_id, 32),
            kind="reference",
            group="reference",
            detail=f"{ref_table}:{ref_id}",
        )
        _add_edge(edges, source=ref_node, target=item_id, label=label, kind="reference")


def _build_graph(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    recent_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    recent: list[dict[str, Any]] = []
    workspace_node = f"workspace:{workspace_id}"
    _add_node(
        nodes,
        node_id=workspace_node,
        label=workspace_id,
        kind="workspace",
        group="workspace",
        status="active",
    )

    for group_id, (label, detail) in _GROUPS.items():
        group_node = f"group:{group_id}"
        _add_node(
            nodes, node_id=group_node, label=label, kind="group", group=group_id, detail=detail
        )
        _add_edge(edges, source=workspace_node, target=group_node, label="contains", kind="group")

    per_table_limit = max(1, min(recent_limit, 80))
    for spec in _TABLES:
        table = spec["table"]
        count = _count(conn, table, workspace_id=workspace_id)
        counts[table] = count
        table_node = _table_node_id(table)
        _add_node(
            nodes,
            node_id=table_node,
            label=spec["label"],
            kind="table",
            group=spec["group"],
            count=count,
            status="empty" if count == 0 else "active",
        )
        _add_edge(
            edges, source=f"group:{spec['group']}", target=table_node, label="table", kind="table"
        )

        for row in _latest_rows(conn, table, workspace_id=workspace_id, limit=per_table_limit):
            row_id = _row_id(row)
            item_node = _item_node_id(table, row_id)
            row_keys = set(row.keys())
            label = _clip(row[spec["text"]] if spec["text"] in row_keys else row_id)
            updated_at = _row_time(row)
            status = _status(row)
            _add_node(
                nodes,
                node_id=item_node,
                label=label or row_id,
                kind=table,
                group=spec["group"],
                status=status,
                updated_at=updated_at,
                detail=row_id,
            )
            _add_edge(edges, source=table_node, target=item_node, label="latest", kind="latest")
            _add_reference(nodes, edges, table=table, item_id=item_node, row=row)
            recent.append(
                {
                    "id": row_id,
                    "table": table,
                    "label": label,
                    "status": status,
                    "updated_at": updated_at,
                }
            )

    recent.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return list(nodes.values()), list(edges.values()), counts, recent[: max(10, recent_limit * 4)]


def _event_label(table: str, row: sqlite3.Row, fallback: str) -> str:
    keys = set(row.keys())
    for key in ("title", "name", "summary", "goal", "path", "raw_text", "text", "evidence"):
        if key in keys and row[key]:
            return _clip(row[key], 110)
    return fallback


def _stage_latest_event(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    tables: list[str],
) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for table in tables:
        rows = _latest_rows(conn, table, workspace_id=workspace_id, limit=1)
        if not rows:
            continue
        row = rows[0]
        row_time = _row_time(row) or ""
        event = {
            "id": _row_id(row),
            "table": table,
            "label": _event_label(table, row, _row_id(row)),
            "status": _status(row),
            "updated_at": row_time or None,
        }
        if latest is None or row_time > str(latest.get("updated_at") or ""):
            latest = event
    return latest


def _build_process(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    counts: dict[str, int],
    recent: list[dict[str, Any]],
) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    for stage in _PROCESS_STAGES:
        tables = list(stage["tables"])
        total = sum(counts.get(table, 0) for table in tables)
        latest = _stage_latest_event(conn, workspace_id=workspace_id, tables=tables)
        status = "empty" if total == 0 else "active"
        if stage["id"] == "governance" and counts.get("maintenance_events", 0):
            status = "review"
        stages.append(
            {
                "id": stage["id"],
                "label": stage["label"],
                "verb": stage["verb"],
                "tables": tables,
                "count": total,
                "status": status,
                "latest": latest,
            }
        )

    events = [
        {
            **event,
            "stage": _TABLE_TO_STAGE.get(event["table"], "capture"),
        }
        for event in recent
    ]
    return {"stages": stages, "edges": _PROCESS_EDGES, "events": events}


def _signature(counts: dict[str, int], recent: list[dict[str, Any]]) -> str:
    raw = repr(
        (
            sorted(counts.items()),
            [(item["table"], item["id"], item.get("updated_at")) for item in recent[:25]],
        )
    )
    return hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()


def _maintenance_warnings(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "maintenance_events"):
        return []
    events = list_maintenance_events(
        conn,
        workspace_id=workspace_id,
        statuses=[MaintenanceEventStatus.OPEN],
        limit=limit,
    )
    return [
        {
            "event_id": event.id,
            "kind": event.kind,
            "severity": event.severity.value,
            "status": event.status.value,
            "summary": event.summary,
            "details": event.details,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "created_at": event.created_at,
        }
        for event in events
    ]


@router.get("/memory/ui/state")
def memory_ui_state(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str | None = Query(default=None),
    recent_limit: int = Query(default=80, ge=1, le=100),
) -> dict[str, Any]:
    selected_workspace = workspace_id or settings.workspace_id
    ensure_workspace_allowed(selected_workspace, settings)
    nodes, edges, counts, recent = _build_graph(
        conn,
        workspace_id=selected_workspace,
        recent_limit=recent_limit,
    )
    process = _build_process(
        conn,
        workspace_id=selected_workspace,
        counts=counts,
        recent=recent,
    )
    latest_events = ui_telemetry.snapshot(workspace_id=selected_workspace, limit=80)
    graph_deltas = ui_telemetry.graph_deltas(workspace_id=selected_workspace, limit=50)
    active_requests = ui_telemetry.active_requests(workspace_id=selected_workspace)
    return {
        "status": "ok",
        "workspace_id": selected_workspace,
        "workspaces": _available_workspaces(conn, settings),
        "registered_workspaces": _registered_workspaces(settings, selected_workspace),
        "hub_mode": settings.hub_mode,
        "generated_at": iso_now(),
        "db_path": str(settings.db_path),
        "vector_path": str(settings.vector_db_path),
        "counts": counts,
        "warnings": _maintenance_warnings(conn, workspace_id=selected_workspace),
        "graph": {"nodes": nodes, "edges": edges},
        "process": process,
        "recent": recent,
        "latest_events": latest_events,
        "graph_deltas": graph_deltas,
        "active_requests": active_requests,
        "signature": _signature(counts, recent),
    }


@router.get("/memory/ui/events")
def memory_ui_events(
    settings: SettingsDep,
    workspace_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    once: bool = Query(default=False),
) -> StreamingResponse:
    selected_workspace = workspace_id or settings.workspace_id
    ensure_workspace_allowed(selected_workspace, settings)
    return StreamingResponse(
        event_stream(workspace_id=selected_workspace, since=since, once=once),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
