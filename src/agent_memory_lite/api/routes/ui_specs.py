"""Static specs for the observatory UI (groups, tables, process stages)."""

from __future__ import annotations

from typing import Any

GROUPS: dict[str, tuple[str, str]] = {
    "episodic": ("Episodic log", "What happened"),
    "retrieval": ("Retrieval", "Searchable chunks and files"),
    "research": ("Research lab", "Theories, evidence, experiments"),
    "capability": ("Capabilities", "Roles, skills, playbooks"),
    "governance": ("Governance", "Decisions and instructions"),
    "operations": ("Operations", "Tasks, candidates, maintenance"),
    "feedback": ("Feedback", "User ranking signal"),
}

TABLES: list[dict[str, str]] = [
    {"table": "episodes", "label": "Episodes", "group": "episodic", "text": "raw_text"},
    {"table": "chunks", "label": "Chunks", "group": "retrieval", "text": "text"},
    {"table": "files", "label": "Files", "group": "retrieval", "text": "path"},
    {"table": "decisions", "label": "Decisions", "group": "governance", "text": "title"},
    {"table": "behaviors", "label": "Behavior", "group": "governance", "text": "name"},
    {"table": "theories", "label": "Theories", "group": "research", "text": "title"},
    {"table": "theory_evidence", "label": "Evidence", "group": "research", "text": "summary"},
    {
        "table": "experiments",
        "label": "Experiments",
        "group": "research",
        "text": "title",
    },
    {"table": "experiment_results", "label": "Results", "group": "research", "text": "summary"},
    {"table": "snapshots", "label": "Snapshots", "group": "research", "text": "title"},
    {"table": "insights", "label": "Insights", "group": "research", "text": "summary"},
    {"table": "concepts", "label": "Concepts", "group": "research", "text": "name"},
    {"table": "skills", "label": "Capabilities", "group": "capability", "text": "name"},
    {
        "table": "capability_links",
        "label": "Capability links",
        "group": "capability",
        "text": "relation",
    },
    {"table": "tasks", "label": "Tasks", "group": "operations", "text": "goal"},
    {
        "table": "candidates",
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

TIME_COLUMNS = ("updated_at", "created_at", "observed_at", "valid_from", "last_indexed_at")

PROCESS_STAGES: list[dict[str, Any]] = [
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
        "tables": ["behaviors", "decisions", "tasks"],
    },
    {
        "id": "research",
        "label": "Research",
        "verb": "tracks hypotheses and evidence",
        "tables": [
            "theories",
            "theory_evidence",
            "experiments",
            "experiment_results",
            "snapshots",
            "insights",
            "concepts",
        ],
    },
    {
        "id": "capabilities",
        "label": "Capabilities",
        "verb": "links roles, skills, playbooks",
        "tables": ["skills", "capability_links"],
    },
    {
        "id": "governance",
        "label": "Govern",
        "verb": "keeps trust work visible",
        "tables": ["candidates", "maintenance_events"],
    },
]

TABLE_TO_STAGE = {table: stage["id"] for stage in PROCESS_STAGES for table in stage["tables"]}

PROCESS_EDGES = [
    {"source": "capture", "target": "index", "label": "chunk"},
    {"source": "index", "target": "retrieve", "label": "search"},
    {"source": "retrieve", "target": "context", "label": "rank"},
    {"source": "context", "target": "research", "label": "reason"},
    {"source": "research", "target": "capabilities", "label": "shape"},
    {"source": "capabilities", "target": "governance", "label": "verify"},
]
