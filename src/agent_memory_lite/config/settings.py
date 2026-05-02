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

    # API
    api_port: int = Field(default=8765, ge=1, le=65535, validation_alias="MEMORY_API_PORT")
    require_api_token: bool = Field(
        default=False,
        validation_alias="MEMORY_REQUIRE_API_TOKEN",
    )
    audit_api_auth_failures: bool = Field(
        default=False,
        validation_alias="MEMORY_AUDIT_API_AUTH_FAILURES",
    )
    api_token_file: Path = Field(
        default=Path(".agent_memory/token"),
        validation_alias="MEMORY_API_TOKEN_FILE",
    )

    # Storage
    db_path: Path = Field(
        default=Path(".agent_memory/memory.db"),
        validation_alias="MEMORY_DB_PATH",
    )
    vector_db_path: Path = Field(
        default=Path(".agent_memory/vectors.lance"),
        validation_alias="VECTOR_DB_PATH",
    )

    # Workspace (single-workspace v1)
    workspace_id: str = Field(default="default", validation_alias="MEMORY_WORKSPACE_ID")
    forbid_default_workspace: bool = Field(
        default=False,
        validation_alias="MEMORY_FORBID_DEFAULT_WORKSPACE",
    )
    strict_workspace_isolation: bool = Field(
        default=False,
        validation_alias="MEMORY_STRICT_WORKSPACE_ISOLATION",
    )
    enforce_workspace_manifest: bool = Field(
        default=True,
        validation_alias="MEMORY_ENFORCE_WORKSPACE_MANIFEST",
    )
    hub_mode: bool = Field(
        default=False,
        validation_alias="MEMORY_HUB_MODE",
    )
    workspaces_file: Path = Field(
        default=Path.home() / ".agent_memory" / "workspaces.json",
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

    # Episode dedup — when enabled, ingest_episode embeds the new
    # raw_text and compares against the last N episodes' chunk
    # embeddings. Cosine similarity > threshold returns the existing
    # episode_id with was_duplicate=True instead of inserting a near-
    # identical row. Off by default so existing tests + workflows are
    # unaffected.
    episode_dedup_enabled: bool = Field(
        default=False, validation_alias="MEMORY_EPISODE_DEDUP_ENABLED"
    )
    episode_dedup_threshold: float = Field(
        default=0.92,
        ge=0.0,
        le=1.0,
        validation_alias="MEMORY_EPISODE_DEDUP_THRESHOLD",
    )
    episode_dedup_window: int = Field(
        default=50, ge=1, le=500, validation_alias="MEMORY_EPISODE_DEDUP_WINDOW"
    )

    # Confidence decay — multiply chunk hit scores by an exponential
    # age decay so 6-month-old episodes don't out-rank yesterday's
    # work just because they share keywords. Half-life is in days; an
    # item ``half_life_days`` old gets multiplied by 0.5. Disabled by
    # default so the score pipeline is unchanged.
    confidence_decay_enabled: bool = Field(
        default=False, validation_alias="MEMORY_CONFIDENCE_DECAY_ENABLED"
    )
    confidence_decay_half_life_days: float = Field(
        default=14.0, gt=0.0, validation_alias="MEMORY_CONFIDENCE_DECAY_HALF_LIFE_DAYS"
    )

    # Conflict detection — when a new decision or theory is written,
    # scan the workspace for existing items whose title/claim shares
    # ``conflict_detect_threshold`` Jaccard word overlap. If found, a
    # ``potential_conflict`` maintenance event is written with both
    # ids so the agent / hygiene script surfaces them for review.
    conflict_detect_enabled: bool = Field(
        default=False, validation_alias="MEMORY_CONFLICT_DETECT_ENABLED"
    )
    conflict_detect_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        validation_alias="MEMORY_CONFLICT_DETECT_THRESHOLD",
    )

    # Token-aware compaction watchdog. When the chunk count exceeds
    # ``compact_trigger_threshold_chunks`` and there are stale chunks
    # older than 90 days, a ``compaction_due`` maintenance event is
    # emitted. The probe never runs compaction itself; the operator
    # decides when to call /memory/compact. 0 = disabled (default).
    compact_trigger_threshold_chunks: int = Field(
        default=0,
        ge=0,
        validation_alias="MEMORY_COMPACT_TRIGGER_THRESHOLD_CHUNKS",
    )

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
