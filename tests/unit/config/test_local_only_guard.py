from __future__ import annotations

import pytest

from agent_memory_lite.config.local_only_guard import (
    LocalOnlyError,
    assert_local_only,
)


def test_accepts_loopback_url(settings_factory) -> None:
    s = settings_factory(LLM_BASE_URL="http://127.0.0.1:11434")
    assert_local_only(s, env={})


def test_accepts_localhost_alias(settings_factory) -> None:
    s = settings_factory(LLM_BASE_URL="http://localhost:11434")
    assert_local_only(s, env={})


def test_rejects_openai(settings_factory) -> None:
    s = settings_factory(LLM_BASE_URL="https://api.openai.com/v1")
    with pytest.raises(LocalOnlyError, match=r"api\.openai\.com"):
        assert_local_only(s, env={})


def test_rejects_anthropic(settings_factory) -> None:
    s = settings_factory(EMBEDDING_BASE_URL="https://api.anthropic.com")
    with pytest.raises(LocalOnlyError, match=r"api\.anthropic\.com"):
        assert_local_only(s, env={})


def test_rejects_non_loopback_private_ip(settings_factory) -> None:
    s = settings_factory(LLM_BASE_URL="http://10.0.0.1:11434")
    with pytest.raises(LocalOnlyError, match="loopback"):
        assert_local_only(s, env={})


def test_rejects_telemetry_env(settings_factory) -> None:
    s = settings_factory()
    with pytest.raises(LocalOnlyError, match="POSTHOG_API_KEY"):
        assert_local_only(s, env={"POSTHOG_API_KEY": "phc_demo"})


def test_rejects_openai_api_key_env(settings_factory) -> None:
    s = settings_factory()
    with pytest.raises(LocalOnlyError, match="OPENAI_API_KEY"):
        assert_local_only(s, env={"OPENAI_API_KEY": "sk-demo"})


def test_disabled_when_local_only_false(settings_factory) -> None:
    s = settings_factory(
        LOCAL_ONLY="false",
        ALLOW_REMOTE_PROVIDERS="true",
        LLM_BASE_URL="https://api.openai.com",
    )
    assert_local_only(s, env={"OPENAI_API_KEY": "sk-demo"})
