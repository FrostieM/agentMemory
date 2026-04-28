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
