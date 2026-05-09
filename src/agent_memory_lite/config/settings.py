"""Frozen application settings, populated from environment / .env file.

The class exposes only the fields the service actually uses. New settings are added
explicitly here so the local-only guard can audit every URL.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

EmbeddingBackend = Literal["sentence_transformers", "ollama"]
VectorBackend = Literal["lancedb", "sqlite_vec"]
LLMBackend = Literal["ollama"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # Core flags
    local_only: bool = Field(default=True, validation_alias="LOCAL_ONLY")
    allow_remote_providers: bool = Field(default=False, validation_alias="ALLOW_REMOTE_PROVIDERS")

    # API + storage + workspace (single-workspace v1)
    api_port: int = Field(8765, ge=1, le=65535, validation_alias="MEMORY_API_PORT")
    require_api_token: bool = Field(False, validation_alias="MEMORY_REQUIRE_API_TOKEN")
    audit_api_auth_failures: bool = Field(False, validation_alias="MEMORY_AUDIT_API_AUTH_FAILURES")
    # 1.2.4: write a lightweight audit_log row for read operations
    # (memory_search, memory_get_context). Required for /memory/telemetry
    # to count the agent's search rate; without it telemetry can only
    # measure writes. Default true because the perf cost is microseconds
    # per call and the operator-facing measurement value is high. Set to
    # false via env if your audit_log volume budget is tight.
    audit_read_operations: bool = Field(True, validation_alias="MEMORY_AUDIT_READS")
    api_token_file: Path = Field(
        Path(".agent_memory/token"), validation_alias="MEMORY_API_TOKEN_FILE"
    )
    db_path: Path = Field(Path(".agent_memory/memory.db"), validation_alias="MEMORY_DB_PATH")
    vector_db_path: Path = Field(
        Path(".agent_memory/vectors.lance"), validation_alias="VECTOR_DB_PATH"
    )
    workspace_id: str = Field("default", validation_alias="MEMORY_WORKSPACE_ID")
    forbid_default_workspace: bool = Field(
        False, validation_alias="MEMORY_FORBID_DEFAULT_WORKSPACE"
    )
    strict_workspace_isolation: bool = Field(
        False, validation_alias="MEMORY_STRICT_WORKSPACE_ISOLATION"
    )
    enforce_workspace_manifest: bool = Field(
        True, validation_alias="MEMORY_ENFORCE_WORKSPACE_MANIFEST"
    )
    hub_mode: bool = Field(False, validation_alias="MEMORY_HUB_MODE")
    workspaces_file: Path = Field(
        Path.home() / ".agent_memory" / "workspaces.json",
        validation_alias="MEMORY_WORKSPACES_FILE",
    )

    # Embeddings
    embedding_backend: EmbeddingBackend = Field(
        default="sentence_transformers", validation_alias="EMBEDDING_BACKEND"
    )
    embedding_model: str = Field(
        default="intfloat/multilingual-e5-small",
        validation_alias="EMBEDDING_MODEL",
    )
    embedding_base_url: str | None = Field(default=None, validation_alias="EMBEDDING_BASE_URL")
    embedding_batch_size: int = Field(default=32, ge=1, validation_alias="EMBEDDING_BATCH_SIZE")

    # Vector store
    vector_backend: VectorBackend = Field(default="lancedb", validation_alias="VECTOR_BACKEND")

    # LLM (mandatory for extraction)
    llm_backend: LLMBackend = Field(default="ollama", validation_alias="LLM_BACKEND")
    llm_base_url: str = Field(default="http://127.0.0.1:11434", validation_alias="LLM_BASE_URL")
    llm_model: str = Field(default="qwen2.5:7b-instruct", validation_alias="LLM_MODEL")
    ollama_probe_skip: bool = Field(default=False, validation_alias="OLLAMA_PROBE_SKIP")

    # 2.1.3 LLM narrative + 2.1.4 similar_signature soft edges.
    # fmt: off
    llm_narrative_enabled: bool = Field(False, validation_alias="MEMORY_LLM_NARRATIVE_ENABLED")
    llm_narrative_min_symbols: int = Field(3, ge=1, le=100, validation_alias="MEMORY_LLM_NARRATIVE_MIN_SYMBOLS")
    llm_narrative_timeout_sec: float = Field(10.0, gt=0.0, le=120.0, validation_alias="MEMORY_LLM_NARRATIVE_TIMEOUT_SEC")
    llm_narrative_max_input_chars: int = Field(4000, ge=200, le=20000, validation_alias="MEMORY_LLM_NARRATIVE_MAX_INPUT_CHARS")
    similar_sig_enabled: bool = Field(True, validation_alias="MEMORY_SIMILAR_SIG_ENABLED")
    similar_sig_threshold: float = Field(0.7, ge=0.0, le=1.0, validation_alias="MEMORY_SIMILAR_SIG_THRESHOLD")
    similar_sig_scan_limit: int = Field(200, ge=1, le=2000, validation_alias="MEMORY_SIMILAR_SIG_SCAN_LIMIT")
    # fmt: on

    # Memory-quality features. As of the 1.1.0 calibration, these
    # default ON: the v1.4-v1.9 + v2 loops are calibrated against real
    # copyBot data (see docs/V1_1_0_CALIBRATION.md) and the older
    # opt-ins are non-destructive. To restore the v1.0.x baseline behaviour,
    # explicitly set the flag to ``false`` in the environment / .env file —
    # tests/invariants/test_v2_parity.py locks that path in.
    episode_dedup_enabled: bool = Field(True, validation_alias="MEMORY_EPISODE_DEDUP_ENABLED")
    episode_dedup_threshold: float = Field(
        0.92, ge=0.0, le=1.0, validation_alias="MEMORY_EPISODE_DEDUP_THRESHOLD"
    )
    episode_dedup_window: int = Field(
        50, ge=1, le=500, validation_alias="MEMORY_EPISODE_DEDUP_WINDOW"
    )
    confidence_decay_enabled: bool = Field(True, validation_alias="MEMORY_CONFIDENCE_DECAY_ENABLED")
    confidence_decay_half_life_days: float = Field(
        14.0, gt=0.0, validation_alias="MEMORY_CONFIDENCE_DECAY_HALF_LIFE_DAYS"
    )
    conflict_detect_enabled: bool = Field(True, validation_alias="MEMORY_CONFLICT_DETECT_ENABLED")
    # fmt: off
    conflict_detect_threshold: float = Field(0.6, ge=0.0, le=1.0, validation_alias="MEMORY_CONFLICT_DETECT_THRESHOLD")
    compact_trigger_threshold_chunks: int = Field(0, ge=0, validation_alias="MEMORY_COMPACT_TRIGGER_THRESHOLD_CHUNKS")
    # fmt: on

    # v1.4 feedback-aware scoring + v1.5 capability maturity. EWMA aggregator
    # runs on demand via /memory/feedback_summary; capability counters move
    # via list_agent_capabilities (mark_used) and complete-task hooks. Self-
    # loop filter and per-day cap defend against an agent inflating its
    # own feedback. Capability stale_days powers the hygiene_report finding.
    feedback_ewma_enabled: bool = Field(True, validation_alias="MEMORY_FEEDBACK_EWMA_ENABLED")
    feedback_halflife_days: float = Field(
        14.0, gt=0.0, validation_alias="MEMORY_FEEDBACK_HALFLIFE_DAYS"
    )
    feedback_exclude_self_loop: bool = Field(
        True, validation_alias="MEMORY_FEEDBACK_EXCLUDE_SELF_LOOP"
    )
    feedback_max_per_day_per_source: int = Field(
        10, ge=1, validation_alias="MEMORY_FEEDBACK_MAX_PER_DAY_PER_SOURCE"
    )
    # v2.1 implicit feedback: derive feedback rows from existing operator
    # actions (archive/promote/link). Calibrated on copyBot (158 rows from
    # 1370 audit entries; 95% rank churn, no spurious inversions).
    implicit_feedback_enabled: bool = Field(
        True, validation_alias="MEMORY_IMPLICIT_FEEDBACK_ENABLED"
    )
    capability_maturity_enabled: bool = Field(
        True, validation_alias="MEMORY_CAPABILITY_MATURITY_ENABLED"
    )
    capability_decay_days: int = Field(30, ge=1, validation_alias="MEMORY_CAPABILITY_DECAY_DAYS")
    capability_stale_days: int = Field(60, ge=1, validation_alias="MEMORY_CAPABILITY_STALE_DAYS")
    behavior_apply_tracking_enabled: bool = Field(
        True, validation_alias="MEMORY_BEHAVIOR_APPLY_TRACKING_ENABLED"
    )

    # v1.6 cold-memory lifecycle. Tracking writes last_retrieved_at on the
    # top-K (cheap, batched audit). Auto-queue emits cold_candidate
    # maintenance events for rows untouched > cold_stale_days; review queue
    # surfaces them, archival stays human-driven via the operator route.
    cold_tracking_enabled: bool = Field(True, validation_alias="MEMORY_COLD_TRACKING_ENABLED")
    cold_auto_queue_enabled: bool = Field(True, validation_alias="MEMORY_COLD_AUTO_QUEUE_ENABLED")
    cold_stale_days: int = Field(60, ge=1, validation_alias="MEMORY_COLD_STALE_DAYS")
    cold_tracking_audit_batch_size: int = Field(
        100, ge=1, validation_alias="MEMORY_COLD_AUDIT_BATCH_SIZE"
    )

    # v1.7 theory -> decision-candidate bridge. Validated theories with
    # >= min_evidence supporting evidence rows surface as pending
    # decision_candidates. The bridge NEVER writes to decisions directly —
    # promote requires explicit operator action via
    # /memory/decision_candidates/{id}/promote.
    theory_bridge_enabled: bool = Field(True, validation_alias="MEMORY_THEORY_BRIDGE_ENABLED")
    theory_bridge_min_evidence: int = Field(
        3, ge=1, validation_alias="MEMORY_THEORY_BRIDGE_MIN_EVIDENCE"
    )

    # v1.8 reflective compaction; v1.9 hygiene recurrence + sentinel
    # persistence; v2.3 trigger-on-traffic sentinel scheduler. See
    # docs/V1_1_0.md for the env-flag map.
    reflective_compact_enabled: bool = Field(
        True, validation_alias="MEMORY_REFLECTIVE_COMPACT_ENABLED"
    )
    # Age threshold for ``summarize_old_episodes`` — only episodes older
    # than this number of days are summarized + scanned for lesson
    # candidates. Default 30 keeps old behaviour (lessons are
    # "experience accumulated over time"); operators on young
    # workspaces can lower to 7 or 14 to start getting reflective
    # insights sooner. Diagnosed in 1.2.5: copyBot @ 13 days had 0
    # insight_candidates because no episode crossed the 30-day bar.
    compact_age_days: int = Field(30, ge=1, validation_alias="MEMORY_COMPACT_AGE_DAYS")
    lesson_min_support_episodes: int = Field(
        4, ge=2, validation_alias="MEMORY_LESSON_MIN_SUPPORT_EPISODES"
    )
    lesson_max_per_run: int = Field(10, ge=1, le=50, validation_alias="MEMORY_LESSON_MAX_PER_RUN")
    hygiene_persist_enabled: bool = Field(True, validation_alias="MEMORY_HYGIENE_PERSIST_ENABLED")
    sentinel_persist_enabled: bool = Field(True, validation_alias="MEMORY_SENTINEL_PERSIST_ENABLED")
    recurrence_threshold: int = Field(3, ge=1, validation_alias="MEMORY_RECURRENCE_THRESHOLD")
    sentinel_autorun_hours: float = Field(
        6.0, ge=0.0, validation_alias="MEMORY_SENTINEL_AUTORUN_HOURS"
    )

    # v1.10 correction-aware learning loop — Hook reads transcript_path
    # JSONL, ingests (claim, correction) pairs, CorrectionExtractor
    # proposes memory_candidate(kind=CORRECTION) for review.
    # fmt: off
    correction_detect_enabled: bool = Field(True, validation_alias="MEMORY_CORRECTION_DETECT_ENABLED")
    correction_transcript_read_enabled: bool = Field(True, validation_alias="MEMORY_CORRECTION_TRANSCRIPT_READ_ENABLED")
    correction_min_user_len: int = Field(30, ge=1, validation_alias="MEMORY_CORRECTION_MIN_USER_LEN")
    correction_min_agent_len: int = Field(50, ge=1, validation_alias="MEMORY_CORRECTION_MIN_AGENT_LEN")
    correction_min_confidence: float = Field(0.5, ge=0.0, le=1.0, validation_alias="MEMORY_CORRECTION_MIN_CONFIDENCE")
    correction_max_per_day: int = Field(20, ge=1, validation_alias="MEMORY_CORRECTION_MAX_PER_DAY")
    correction_pair_window_min: int = Field(30, ge=1, validation_alias="MEMORY_CORRECTION_PAIR_WINDOW_MIN")
    # fmt: on

    # Logging
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    def url_fields(self) -> dict[str, str]:
        """Return non-empty URL settings, keyed by env-style name."""
        urls: dict[str, str] = {"LLM_BASE_URL": self.llm_base_url}
        if self.embedding_base_url:
            urls["EMBEDDING_BASE_URL"] = self.embedding_base_url
        return urls


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: clear the lru_cache so a new env can be picked up."""
    get_settings.cache_clear()
