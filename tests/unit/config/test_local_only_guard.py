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


def test_local_only_false_relaxes_loopback_but_not_denylist(settings_factory) -> None:
    """Round-2 audit (F1): LOCAL_ONLY=false / ALLOW_REMOTE_PROVIDERS=true
    relaxes ONLY the loopback requirement — a non-cloud on-prem host is
    allowed — but the cloud denylist and telemetry kill-list stay
    unconditional. Pre-fix, either flag disabled the whole guard and
    a cloud URL + cloud API key sailed through."""
    # On-prem non-loopback host: allowed once the flag relaxes loopback.
    s_ok = settings_factory(
        LOCAL_ONLY="false",
        ALLOW_REMOTE_PROVIDERS="true",
        LLM_BASE_URL="http://192.168.1.50:11434",
    )
    assert_local_only(s_ok, env={})
    # A KNOWN CLOUD host is still rejected even with both flags off.
    s_cloud = settings_factory(
        LOCAL_ONLY="false",
        ALLOW_REMOTE_PROVIDERS="true",
        LLM_BASE_URL="https://api.openai.com",
    )
    with pytest.raises(LocalOnlyError, match="denylist"):
        assert_local_only(s_cloud, env={})
    # Telemetry / cloud-credential env vars are still rejected.
    s_tel = settings_factory(LOCAL_ONLY="false", ALLOW_REMOTE_PROVIDERS="true")
    with pytest.raises(LocalOnlyError, match="OPENAI_API_KEY"):
        assert_local_only(s_tel, env={"OPENAI_API_KEY": "sk-demo"})


@pytest.mark.parametrize(
    "host",
    [
        "https://api.perplexity.ai/chat/completions",
        "https://api.replicate.com/v1/predictions",
        "https://api-inference.huggingface.co/models/foo",
        "https://router.huggingface.co/v1/chat",
        "https://api.cerebras.ai/v1",
        "https://api.lepton.ai/api/v1/chat",
        "https://abc.qdrant.io/collections",
        "https://abc.chromadb.cloud",
        "https://workspace.turbopuffer.com",
    ],
)
def test_rejects_v3_denylist_additions(settings_factory, host: str) -> None:
    """v3.0.0-final: the cloud denylist gained perplexity / replicate /
    huggingface / cerebras / lepton / qdrant / chromadb / turbopuffer
    entries to cover the next tier of inference + vector hosts that
    have surfaced since v1.x."""
    s = settings_factory(LLM_BASE_URL=host)
    with pytest.raises(LocalOnlyError):
        assert_local_only(s, env={})
