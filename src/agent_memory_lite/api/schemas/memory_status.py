"""Wire schema for ``GET /memory/status``.

Designed to answer the read-side adoption question another AI agent
flagged: "I don't know how much of the workspace is ingested, so I
default to Grep". ``/memory/status`` returns the coverage numbers
in one cheap call so the agent can stop guessing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MemoryCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions_active: int
    decisions_total: int
    theories_active: int
    theories_total: int
    behaviors_active: int
    behaviors_total: int
    capabilities_active_total: int
    episodes_total: int
    chunks_total: int
    insights_new: int
    pending_candidates: int


class CodeMemoryCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: int
    chunks_with_symbols: int
    symbols: int
    edges: int
    soft_edges: int
    versions: int


class AdoptionRatios(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions_with_source_episode_ratio: float
    decisions_linked_to_capability_ratio: float
    behaviors_fired_ratio: float


class EnvironmentInfo(BaseModel):
    """Server / anchor / registry / providers introspection.

    Lets the agent answer "am I on the right workspace?" + "what
    workspaces does this server know about?" + "what providers are
    actually wired?" without stitching ``/health`` +
    ``/memory/workspaces``. Closes the silent-misconfig footgun
    audit-driven by 2026-05-19 routing bug.

    P2 extension (audit-round-1 A#6): added embedding/vector/llm
    backend names + the HTTP base URL so an agent that finds search
    empty can correlate immediately ("is the embedding provider even
    wired?") instead of guessing.
    """

    model_config = ConfigDict(extra="forbid")

    hub_mode: bool
    anchor_workspace_id: str
    anchor_db_path: str
    anchor_vector_db_path: str
    forbid_default_workspace: bool
    strict_workspace_isolation: bool
    registry_path: str
    registry_workspaces: list[str]
    # None when the registry was absent (clean empty) or parsed successfully;
    # 'corrupt:json' / 'corrupt:io' / 'corrupt:shape' / 'corrupt:unknown' when the
    # registry file existed but could not be read -- so the operator sees
    # "registry unavailable", not a misleading empty registry_workspaces.
    registry_load_error: str | None = None
    applied_migrations: list[str]
    # P2 additions — runtime stack visibility.
    embedding_backend: str = ""
    embedding_model: str = ""
    vector_backend: str = ""
    llm_backend: str = ""
    llm_model: str = ""
    http_base_url: str = ""
    # HF offline posture (config/offline_bootstrap) — lets an operator see
    # whether the model hub is actually pinned offline without reading env.
    hf_auto_offline: bool = False
    hf_offline_active: bool = False


class DegradationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    severity: str
    count: int


class DegradationSummary(BaseModel):
    """Open ``maintenance_events`` surfaced on the FAST status path so an agent
    or operator sees substrate degradation in ONE read.

    Reliability audit (M6/L4): degradation facts (``vector_upsert_failed``,
    ``durable_fts_sync_failed``, ``brain_pass_step_failed``) are persisted as
    maintenance_events but were visible ONLY through the
    30-60s deep ``/health?deep=true`` audit -- so a degraded memory substrate
    looked healthy on every fast surface. ``degraded`` is True when any open
    event is ERROR/CRITICAL severity (WARNING housekeeping does not flip it)."""

    model_config = ConfigDict(extra="forbid")

    open_maintenance_events: int = 0
    degraded: bool = False
    recent_degradations: list[DegradationItem] = Field(default_factory=list)


class ActiveMemoryCounts(BaseModel):
    """v3.1 active-memory dashboard — one snapshot of every vector's
    current state so the operator can see "is the brain actually
    thinking right now?" without 4 separate route calls.

    Each ``..._available`` flag mirrors the per-route ``available``
    semantics: ``False`` when the scanner's required schema is missing
    (e.g. legacy-only DB w/o canonical insights / outcome_score).
    """

    model_config = ConfigDict(extra="forbid")

    # Vector 1 — experiment proposal.
    open_proposals: int = 0
    proposals_available: bool = True
    proposals_enabled: bool = True
    # Vector 3 — blindspot detection.
    blindspots: int = 0
    blindspots_enabled: bool = True
    # Vector 5 — predictive failure.
    predictive_warnings: int = 0
    predictive_warnings_available: bool = True
    predictive_warnings_enabled: bool = True
    # Vector 5 — persisted warnings in 'new' state (review-queue ready).
    persisted_warnings_new: int = 0


class MemoryStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    workspace_id: str
    memory: MemoryCounts
    code_memory: CodeMemoryCounts
    adoption: AdoptionRatios
    last_episode_at: str | None
    last_decision_at: str | None
    last_ingest_file_at: str | None
    recent_actions_7d: dict[str, int] = Field(default_factory=dict)
    # Always present (cheap GROUP BY): open maintenance_events on the fast path
    # so a degraded substrate is visible without the deep /health audit (M6/L4).
    degradation: DegradationSummary = Field(default_factory=DegradationSummary)
    # Optional environment block — populated when the agent passes
    # ``include_environment=true`` so existing callers see no payload
    # bloat. The block answers "where am I running and what
    # workspaces does this server route to?".
    environment: EnvironmentInfo | None = None
    # v3.1: active-memory vector snapshot. Optional so legacy clients
    # parsing the response don't break on unknown fields (pydantic v2
    # would forbid extras only if the client also uses forbid).
    active_memory: ActiveMemoryCounts | None = None
