"""Per-kind seed handlers for research-context evals.

Split out of ``runner_research.py`` so each module stays under the
SLOC ceiling.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_role,
    upsert_agent_skill,
)
from agent_memory_lite.ingestion.research_writer import (
    distill_insight,
    register_snapshot,
    upsert_domain_concept,
    write_experiment,
)
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.models.capabilities import (
    AgentPlaybookIn,
    AgentRoleIn,
    AgentSkillIn,
)
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.models.research import (
    DomainConceptIn,
    ExperimentIn,
    MemorySnapshotIn,
    ResearchInsightIn,
)
from agent_memory_lite.models.theories import TheoryIn


def _seed_theory(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    del labels
    payload.setdefault("workspace_id", workspace_id)
    return write_theory(conn, TheoryIn.model_validate(payload)).id


def _seed_role(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    del labels
    payload.setdefault("workspace_id", workspace_id)
    return upsert_agent_role(conn, AgentRoleIn.model_validate(payload)).id


def _seed_skill(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    del labels
    payload.setdefault("workspace_id", workspace_id)
    return upsert_agent_skill(conn, AgentSkillIn.model_validate(payload)).id


def _seed_playbook(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    del labels
    payload.setdefault("workspace_id", workspace_id)
    return upsert_agent_playbook(conn, AgentPlaybookIn.model_validate(payload)).id


def _seed_snapshot(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    del labels
    payload.setdefault("workspace_id", workspace_id)
    return register_snapshot(conn, MemorySnapshotIn.model_validate(payload)).id


def _seed_concept(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    del labels
    payload.setdefault("workspace_id", workspace_id)
    return upsert_domain_concept(conn, DomainConceptIn.model_validate(payload)).id


def _seed_experiment(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    payload.setdefault("workspace_id", workspace_id)
    theory_label = payload.pop("theory_label", None)
    snapshot_label = payload.pop("snapshot_label", None)
    if theory_label is not None:
        payload["theory_id"] = labels[str(theory_label)]
    if snapshot_label is not None:
        payload["snapshot_id"] = labels[str(snapshot_label)]
    return write_experiment(conn, ExperimentIn.model_validate(payload)).id


def _seed_insight(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    payload.setdefault("workspace_id", workspace_id)
    target_label = payload.pop("target_label", None)
    if target_label is not None:
        payload["target_id"] = labels[str(target_label)]
    return distill_insight(conn, ResearchInsightIn.model_validate(payload)).id


def _seed_capability_link(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    payload.setdefault("workspace_id", workspace_id)
    target_label = payload.pop("target_label", None)
    capability_label = payload.pop("capability_label", None)
    if target_label is not None:
        payload["target_id"] = labels[str(target_label)]
    if capability_label is not None:
        payload["capability_id"] = labels[str(capability_label)]
    return link_capability(conn, CapabilityLinkIn.model_validate(payload)).id


HANDLERS = {
    "role": _seed_role,
    "skill": _seed_skill,
    "playbook": _seed_playbook,
    "theory": _seed_theory,
    "snapshot": _seed_snapshot,
    "concept": _seed_concept,
    "experiment": _seed_experiment,
    "insight": _seed_insight,
    "capability_link": _seed_capability_link,
}
