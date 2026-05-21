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

A follow-up re-audit found a sibling of F2:
  F2b — HF_ENDPOINT is honored by sentence-transformers /
        huggingface_hub as the model-download base, so HF_ENDPOINT
        pointed at a cloud host leaks the first embedding load (and the
        model name) off-machine even when llm_base_url is loopback. The
        guard now audits every entry in PROVIDER_HOST_ENV_VARS, not
        OLLAMA_HOST alone.

A third re-audit round (proxy + alias) found:
  R3a — HF_HUB_ENDPOINT is the alias older huggingface_hub /
        sentence-transformers pins read instead of HF_ENDPOINT — an
        equivalent off-machine model-download redirect.
  R3b — the standard proxy env vars (HTTP_PROXY / HTTPS_PROXY /
        ALL_PROXY) were uninspected. A proxy is a transport-level
        redirect the URL checks cannot see, so a cloud proxy tunnels a
        loopback-looking request off-machine. The guard now audits the
        proxy vars, and every httpx client pins trust_env=False.

This file locks each fix so a re-audit finds nothing.
"""

from __future__ import annotations

from pathlib import Path

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


# ---------- F2b (re-audit): HF_ENDPOINT is audited too ----------


def test_hf_endpoint_cloud_redirect_rejected() -> None:
    """HF_ENDPOINT is the model-download base for sentence-transformers /
    huggingface_hub. A cloud HF_ENDPOINT leaks the first embedding load
    off-machine and must be rejected even when llm_base_url is loopback."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    with pytest.raises(LocalOnlyError, match="HF_ENDPOINT"):
        assert_local_only(settings, env={"HF_ENDPOINT": "https://huggingface.co"})
    # Bare host:port form is parsed too.
    with pytest.raises(LocalOnlyError, match="HF_ENDPOINT"):
        assert_local_only(settings, env={"HF_ENDPOINT": "api-inference.huggingface.co:443"})


def test_hf_endpoint_loopback_passes() -> None:
    """A loopback HF_ENDPOINT (an on-box model mirror) is fine."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    assert_local_only(settings, env={"HF_ENDPOINT": "http://127.0.0.1:8080"})
    assert_local_only(settings, env={"HF_ENDPOINT": "localhost:8080"})


def test_hf_endpoint_non_loopback_blocked_when_not_relaxed() -> None:
    """A non-cloud non-loopback HF_ENDPOINT is blocked under default
    local_only mode."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    with pytest.raises(LocalOnlyError, match="HF_ENDPOINT"):
        assert_local_only(settings, env={"HF_ENDPOINT": "http://192.168.1.50:8080"})


# ---------- R3a (re-audit): HF_HUB_ENDPOINT, the HF_ENDPOINT alias ----------


def test_hf_hub_endpoint_cloud_redirect_rejected() -> None:
    """HF_HUB_ENDPOINT is the alias older huggingface_hub / sentence-
    transformers pins read instead of HF_ENDPOINT — equally an off-machine
    model-download redirect, so it must be on the audited set."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    with pytest.raises(LocalOnlyError, match="HF_HUB_ENDPOINT"):
        assert_local_only(settings, env={"HF_HUB_ENDPOINT": "https://huggingface.co"})


def test_hf_hub_endpoint_loopback_passes() -> None:
    """A loopback HF_HUB_ENDPOINT is fine."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    assert_local_only(settings, env={"HF_HUB_ENDPOINT": "http://127.0.0.1:8080"})


# ---------- R3b (re-audit): proxy env vars are a transport-level redirect ----------


@pytest.mark.parametrize("proxy_var", ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"])
def test_proxy_env_cloud_redirect_rejected(proxy_var: str) -> None:
    """httpx — and huggingface_hub's own session — honor HTTP_PROXY /
    HTTPS_PROXY / ALL_PROXY. A cloud proxy tunnels a loopback-looking
    request off-machine; the guard never sees a non-local URL. A
    denylisted proxy host must therefore be rejected."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    with pytest.raises(LocalOnlyError, match=proxy_var):
        assert_local_only(settings, env={proxy_var: "https://api.openai.com"})


def test_proxy_env_lowercase_variant_audited() -> None:
    """The lowercase proxy var names (httpx reads both cases) are audited
    too — a denylisted vector host via https_proxy is rejected."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    with pytest.raises(LocalOnlyError, match="https_proxy"):
        assert_local_only(settings, env={"https_proxy": "https://tenant.pinecone.io"})


def test_proxy_env_non_loopback_blocked_when_not_relaxed() -> None:
    """A non-cloud non-loopback proxy is blocked under default local_only
    mode — a strict local-only service must not funnel through a LAN
    proxy unless the operator explicitly opts in."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    with pytest.raises(LocalOnlyError, match="loopback"):
        assert_local_only(settings, env={"HTTP_PROXY": "http://192.168.1.9:3128"})


def test_proxy_env_loopback_passes() -> None:
    """A loopback proxy (a local debugging proxy) is allowed."""
    settings = Settings(LLM_BASE_URL="http://127.0.0.1:11434")  # type: ignore[call-arg]
    assert_local_only(settings, env={"HTTP_PROXY": "http://127.0.0.1:8888"})


def test_proxy_env_cloud_rejected_even_when_remote_providers_relaxed() -> None:
    """ALLOW_REMOTE_PROVIDERS relaxes the loopback requirement, so an
    on-prem proxy is allowed — but a cloud proxy is STILL rejected; the
    denylist is unconditional."""
    s_onprem = Settings(  # type: ignore[call-arg]
        ALLOW_REMOTE_PROVIDERS=True,
        LLM_BASE_URL="http://192.168.1.50:11434",
    )
    assert_local_only(s_onprem, env={"HTTP_PROXY": "http://192.168.1.9:3128"})
    s_cloud = Settings(  # type: ignore[call-arg]
        ALLOW_REMOTE_PROVIDERS=True,
        LLM_BASE_URL="http://192.168.1.50:11434",
    )
    with pytest.raises(LocalOnlyError, match="denylist"):
        assert_local_only(s_cloud, env={"HTTPS_PROXY": "https://api.openai.com"})


# ---------- R3b (re-audit): every httpx client pins trust_env=False ----------


def test_all_httpx_clients_disable_trust_env() -> None:
    """Every httpx.Client / httpx.AsyncClient constructed in the package
    must pass trust_env=False, so proxy / NETRC env vars cannot redirect
    an outbound call past the local-only guard. A new client missing it
    re-opens the transport-level escape hatch."""
    import agent_memory_lite  # noqa: PLC0415

    pkg_root = Path(agent_memory_lite.__file__).resolve().parent
    offenders: list[str] = []
    for py in pkg_root.rglob("*.py"):
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if ("httpx.Client(" in line or "httpx.AsyncClient(" in line) and (
                "trust_env=False" not in line
            ):
                offenders.append(f"{py.name}:{lineno}")
    assert not offenders, f"httpx client(s) missing trust_env=False: {offenders}"
