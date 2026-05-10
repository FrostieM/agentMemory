"""Wire-side schemas for `memory_write_decision`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.schemas._text_guard import SafeText, SafeTextOptional


class WriteDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    title: SafeText = Field(min_length=1)
    decision_text: SafeText = Field(min_length=1)
    rationale: SafeTextOptional = None
    supersedes_decision_id: str | None = None
    source_episode_id: str | None = None
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    importance: float = Field(default=0.8, ge=0.0, le=1.0)
    # 1.3.0: explicit file/symbol references. /memory/explain_diff and
    # /memory/cold_decisions read this field; the agent should populate
    # it when the decision concretely affects specific files.
    references: list[str] = Field(default_factory=list)
    # 2.2 Move 1: when source_episode_id is None and the server has
    # auto_thread_decision_source enabled, the route auto-fills it from
    # the agent's most recent ingest_episode in the same workspace.
    # Pass allow_orphan=True to opt out and write a deliberately
    # untraced decision (e.g. when the decision predates the recording
    # of any episode).
    allow_orphan: bool = False


class CapabilitySuggestionPayload(BaseModel):
    """Server-ranked capability suggestion (Move 3 of v2.2).

    Returned with write_decision / record_with_evidence responses when
    the workspace has roles/skills/playbooks that token-overlap with
    the decision text. Read-only hint — the agent (or operator via
    /ui/review) decides whether to call link_capability with one of
    these picks.
    """

    model_config = ConfigDict(extra="forbid")
    capability_type: str
    capability_id: str
    capability_name: str
    score: float
    snippet: str


class WriteDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    status: str
    valid_from: str
    superseded_decision_id: str | None
    # 2.2 Move 1: server returns the resolved source_episode_id so the
    # agent can tell whether auto-thread succeeded without a follow-up
    # get_object call. ``None`` means the caller passed allow_orphan=True
    # OR the auto-thread heuristic found no recent ingest_episode for
    # this agent in the workspace's window.
    source_episode_id: str | None = None
    capability_suggestions: list[CapabilitySuggestionPayload] = Field(default_factory=list)


class ListDecisionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    include_superseded: bool = False
    limit: int = Field(default=10, ge=1, le=100)
    # ISO-8601 endpoints for created_at filtering. Both inclusive,
    # either side optional. Use cases: "what changed in the last 30
    # days" / "show me only the v2 architecture decisions".
    since: str | None = None
    until: str | None = None


class DecisionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    title: str
    decision_text: str
    rationale: str | None
    status: str
    supersedes_decision_id: str | None
    source_episode_id: str | None
    confidence: float
    importance: float
    valid_from: str
    valid_to: str | None
    created_at: str
    updated_at: str
    pinned: bool = False
    references: list[str] = Field(default_factory=list)


class ListDecisionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[DecisionItem]
