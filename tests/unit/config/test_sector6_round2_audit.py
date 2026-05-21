"""Sector 6 Round-2 adversarial audit — regression locks.

A fresh adversarial agent audited the security boundary:
  F1 — assert_local_only returned early when LOCAL_ONLY=false or
       ALLOW_REMOTE_PROVIDERS=true, so the cloud denylist was skipped
       — "no cloud, ever" became a single env flip
  F2 — OLLAMA_HOST is honored by the Ollama client directly, past the
       guard (llm_base_url could be loopback while OLLAMA_HOST points
       at a cloud inference host)
  F3 — the denylist was exact-match; a trailing-dot FQDN
       (api.openai.com.) resolved identically but bypassed it

This file locks each fix so a re-audit finds nothing.
"""

from __future__ import annotations

import pytest

from agent_memory_lite.config.local_only_guard import (
    LocalOnlyError,
    _host_is_denylisted,
    assert_local_only,
)
from agent_memory_lite.config.settings import Settings

# ---------- F3: trailing-dot FQDN normalisation ----------


def test_denylist_normalises_trailing_dot() -> None:
    """A trailing-dot FQDN resolves identically to the bare host and
    must be caught by the denylist."""
    assert _host_is_denylisted("api.openai.com") is True
    assert _host_is_denylisted("api.openai.com.") is True  # trailing dot
    assert _host_is_denylisted("API.OpenAI.CoM.") is True  # case + dot
    # A genuine loopback host is not denylisted.
    assert _host_is_denylisted("127.0.0.1") is False
    assert _host_is_denylisted("localhost") is False


# ---------- F1: denylist is unconditional ----------


def test_denylist_enforced_even_when_local_only_disabled() -> None:
    """With LOCAL_ONLY=false the loopback requirement relaxes, but a
    KNOWN CLOUD host must STILL be rejected — the denylist is a hard
    floor, not an env-flippable check."""
    settings = Settings(
        LOCAL_ONLY=False,  # type: ignore[call-arg]
        LLM_BASE_URL="https://api.openai.com/v1",
    )
    with pytest.raises(LocalOnlyError, match="denylist"):
        assert_local_only(settings, env={})


def test_denylist_enforced_even_when_remote_providers_allowed() -> None:
    """Same via the ALLOW_REMOTE_PROVIDERS flag."""
    settings = Settings(
        ALLOW_REMOTE_PROVIDERS=True,  # type: ignore[call-arg]
        LLM_BASE_URL="https://api.anthropic.com",
    )
    with pytest.raises(LocalOnlyError, match="denylist"):
        assert_local_only(settings, env={})


def test_loopback_requirement_relaxes_for_on_prem_host() -> None:
    """A NON-cloud, non-loopback host (an on-prem box) is allowed when
    the operator opts in — that is the legitimate use of the flag."""
    settings = Settings(
        ALLOW_REMOTE_PROVIDERS=True,  # type: ignore[call-arg]
        LLM_BASE_URL="http://192.168.1.50:11434",
    )
    # Must NOT raise — on-prem host, not on the cloud denylist.
    assert_local_only(settings, env={})


def test_loopback_host_passes_by_default() -> None:
    """Default (local_only) settings with a loopback LLM URL pass."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    assert_local_only(settings, env={})


def test_non_loopback_blocked_when_not_relaxed() -> None:
    """A non-cloud non-loopback host is still blocked under default
    local_only mode (the loopback requirement is active)."""
    settings = Settings(LLM_BASE_URL="http://192.168.1.50:11434")  # type: ignore[call-arg]
    with pytest.raises(LocalOnlyError, match="loopback"):
        assert_local_only(settings, env={})


# ---------- F2: OLLAMA_HOST is audited ----------


def test_ollama_host_cloud_redirect_rejected() -> None:
    """A cloud OLLAMA_HOST must be rejected even though llm_base_url is
    loopback — the Ollama client honors OLLAMA_HOST directly."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    with pytest.raises(LocalOnlyError, match="OLLAMA_HOST"):
        assert_local_only(settings, env={"OLLAMA_HOST": "https://api.together.xyz"})
    # Bare host:port form is parsed too.
    with pytest.raises(LocalOnlyError, match="OLLAMA_HOST"):
        assert_local_only(settings, env={"OLLAMA_HOST": "api.openai.com:443"})


def test_ollama_host_loopback_passes() -> None:
    """A loopback OLLAMA_HOST is fine."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    assert_local_only(settings, env={"OLLAMA_HOST": "http://127.0.0.1:11434"})
    assert_local_only(settings, env={"OLLAMA_HOST": "localhost:11434"})


def test_ollama_host_non_loopback_blocked_when_not_relaxed() -> None:
    """A non-cloud non-loopback OLLAMA_HOST is blocked under default
    local_only mode."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    with pytest.raises(LocalOnlyError, match="OLLAMA_HOST"):
        assert_local_only(settings, env={"OLLAMA_HOST": "http://192.168.1.50:11434"})
