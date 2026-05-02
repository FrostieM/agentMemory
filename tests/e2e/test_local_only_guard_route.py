from __future__ import annotations

import pytest

from agent_memory_lite.api.app import create_app
from agent_memory_lite.config.local_only_guard import LocalOnlyError


def test_create_app_refuses_cloud_llm_url(settings_factory) -> None:
    settings = settings_factory(LLM_BASE_URL="https://api.openai.com/v1")
    with pytest.raises(LocalOnlyError):
        create_app(settings)


def test_create_app_refuses_cloud_embedding_url(settings_factory) -> None:
    settings = settings_factory(EMBEDDING_BASE_URL="https://api.anthropic.com")
    with pytest.raises(LocalOnlyError):
        create_app(settings)


def test_create_app_starts_with_loopback_urls(settings_factory) -> None:
    settings = settings_factory(LLM_BASE_URL="http://127.0.0.1:11434")
    app = create_app(settings)
    assert app is not None
