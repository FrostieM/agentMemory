from __future__ import annotations

from agent_memory_lite.config.settings import Settings


def test_settings_defaults_match_env(settings_factory) -> None:
    s = settings_factory()
    assert s.local_only is True
    assert s.allow_remote_providers is False
    assert s.api_port == 8765
    assert s.workspace_id == "default"
    assert s.strict_workspace_isolation is False
    assert s.enforce_workspace_manifest is True
    assert s.embedding_backend == "sentence_transformers"
    assert s.embedding_model == "intfloat/multilingual-e5-small"
    assert s.vector_backend == "lancedb"
    assert s.llm_backend == "ollama"
    assert s.llm_model == "qwen2.5:7b-instruct"


def test_settings_url_fields_includes_only_set_values(settings_factory) -> None:
    s = settings_factory()
    urls = s.url_fields()
    assert urls == {"LLM_BASE_URL": "http://127.0.0.1:11434"}


def test_settings_url_fields_includes_embedding_when_set(settings_factory) -> None:
    s = settings_factory(EMBEDDING_BASE_URL="http://127.0.0.1:11434")
    urls = s.url_fields()
    assert urls == {
        "LLM_BASE_URL": "http://127.0.0.1:11434",
        "EMBEDDING_BASE_URL": "http://127.0.0.1:11434",
    }


def test_settings_reads_strict_workspace_isolation(settings_factory) -> None:
    s = settings_factory(
        MEMORY_WORKSPACE_ID="project-a",
        MEMORY_STRICT_WORKSPACE_ISOLATION="true",
    )
    assert s.workspace_id == "project-a"
    assert s.strict_workspace_isolation is True


def test_settings_is_frozen() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    try:
        s.api_port = 9999  # type: ignore[misc]
    except (TypeError, ValueError):
        return
    assert s.api_port == 8765, "Settings should be immutable"
