"""Graph + node/edge builders for the observatory UI.

Reference edges (foreign-key fan-out) live in ``ui_references.py``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.api.routes.ui_db import (
    clip,
    count_rows,
    latest_rows,
    row_id,
    row_status,
    row_time,
)
from agent_memory_lite.api.routes.ui_references import add_references
from agent_memory_lite.api.routes.ui_specs import GROUPS, TABLES


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


def _row_label(row: sqlite3.Row, text_column: str, fallback: str) -> tuple[str | None, str]:
    """Pick the user-supplied short label when present; fall back to the
    table's main text column."""
    keys = set(row.keys())
    short_label: str | None = None
    if "label" in keys and row["label"]:
        short_label = clip(row["label"], 80)
    label = short_label or clip(row[text_column] if text_column in keys else fallback)
    return short_label, label or fallback


def build_graph(
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

    for group_id, (label, detail) in GROUPS.items():
        group_node = f"group:{group_id}"
        _add_node(
            nodes, node_id=group_node, label=label, kind="group", group=group_id, detail=detail
        )
        _add_edge(edges, source=workspace_node, target=group_node, label="contains", kind="group")

    per_table_limit = max(1, min(recent_limit, 80))
    for spec in TABLES:
        table = spec["table"]
        count = count_rows(conn, table, workspace_id=workspace_id)
        counts[table] = count
        table_node = f"table:{table}"
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

        for row in latest_rows(conn, table, workspace_id=workspace_id, limit=per_table_limit):
            item_id = row_id(row)
            item_node = f"{table}:{item_id}"
            short_label, label = _row_label(row, spec["text"], item_id)
            updated_at = row_time(row)
            status = row_status(row)
            _add_node(
                nodes,
                node_id=item_node,
                label=label,
                kind=table,
                group=spec["group"],
                status=status,
                updated_at=updated_at,
                detail=item_id,
            )
            _add_edge(edges, source=table_node, target=item_node, label="latest", kind="latest")
            add_references(nodes, edges, item_id=item_node, row=row)
            recent.append(
                {
                    "id": item_id,
                    "table": table,
                    "label": label,
                    "short_label": short_label,
                    "status": status,
                    "updated_at": updated_at,
                }
            )

    recent.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return list(nodes.values()), list(edges.values()), counts, recent[: max(10, recent_limit * 4)]
