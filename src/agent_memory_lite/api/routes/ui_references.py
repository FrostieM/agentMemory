"""Cross-table reference edges for the observatory UI graph."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.api.routes.ui_db import clip


def _add_node(
    nodes: dict[str, dict[str, Any]],
    *,
    node_id: str,
    label: str,
    kind: str,
    group: str,
    detail: str | None = None,
) -> None:
    nodes.setdefault(
        node_id,
        {
            "id": node_id,
            "label": label,
            "kind": kind,
            "group": group,
            "count": None,
            "status": None,
            "updated_at": None,
            "detail": detail,
        },
    )


def _add_edge(
    edges: dict[str, dict[str, Any]],
    *,
    source: str,
    target: str,
    label: str,
) -> None:
    edge_id = f"{source}->{target}:{label}"
    edges.setdefault(
        edge_id,
        {"id": edge_id, "source": source, "target": target, "label": label, "kind": "reference"},
    )


def add_references(
    nodes: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
    *,
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
        ref_node = f"{ref_table}:{ref_id}"
        _add_node(
            nodes,
            node_id=ref_node,
            label=clip(ref_id, 32),
            kind="reference",
            group="reference",
            detail=f"{ref_table}:{ref_id}",
        )
        _add_edge(edges, source=ref_node, target=item_id, label=label)
