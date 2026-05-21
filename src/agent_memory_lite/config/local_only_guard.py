"""Startup-time guard that refuses to run when configured to talk to non-local hosts.

The guard inspects every URL on `Settings`, the host portion of each URL, and the
process environment for telemetry kill-list variables. Any violation raises
`LocalOnlyError`, which `create_app` lets propagate (the service refuses to start).
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from agent_memory_lite.config.settings import Settings

LOCAL_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})

CLOUD_DENYLIST: frozenset[str] = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "api.cohere.com",
        "api.mistral.ai",
        "api.deepseek.com",
        "api.together.xyz",
        "api.groq.com",
        "api.fireworks.ai",
        "api.x.ai",
        "generativelanguage.googleapis.com",
        "openai.azure.com",
        # v3.0.0-final: cover the next tier of inference endpoints that
        # have become common since v1.x. The guard runs on every
        # outbound URL, so adding here costs nothing but prevents a
        # misconfigured Settings.llm_base_url from leaking work
        # off-machine.
        "api.perplexity.ai",
        "api.replicate.com",
        "api-inference.huggingface.co",
        "huggingface.co",
        "router.huggingface.co",
        "api.cerebras.ai",
        "api.lepton.ai",
        "api.runpod.io",
    }
)

CLOUD_DENYLIST_SUFFIXES: tuple[str, ...] = (
    ".openai.azure.com",
    ".bedrock-runtime.amazonaws.com",
    ".pinecone.io",
    ".weaviate.cloud",
    ".zep.us",
    # Additional vector-DB hosts surfaced since v1.x.
    ".qdrant.io",
    ".chromadb.cloud",
    ".turbopuffer.com",
)

TELEMETRY_KILL_LIST: tuple[str, ...] = (
    "POSTHOG_API_KEY",
    "SENTRY_DSN",
    "MIXPANEL_TOKEN",
    "LANGFUSE_HOST",
    "WANDB_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "COHERE_API_KEY",
)

# Round-2 audit (F2 + re-audit): provider-SDK env vars that redirect
# outbound calls WITHOUT appearing on Settings.url_fields(). The guard's
# model is "audit every outbound URL", but a client library can read an
# env var the guard never sees:
#   * OLLAMA_HOST   — the Ollama client honors it directly.
#   * HF_ENDPOINT / HF_HUB_ENDPOINT — sentence-transformers and
#                     huggingface_hub read either as the model-download
#                     base, so =https://evil leaks the request (and the
#                     model name) off-machine on the FIRST embedding load
#                     even though llm_base_url / embedding_base_url are
#                     loopback. HF_HUB_ENDPOINT is the alias older
#                     huggingface_hub / sentence-transformers pins honor.
# Each is parsed + checked against the denylist + loopback rule, exactly
# like a Settings URL field.
PROVIDER_HOST_ENV_VARS: tuple[str, ...] = ("OLLAMA_HOST", "HF_ENDPOINT", "HF_HUB_ENDPOINT")

# Round-3 audit: httpx clients — and huggingface_hub's OWN internal HTTP
# session, which the guard cannot pass options to — honor the standard
# proxy env vars. A proxy is a TRANSPORT-level redirect the URL checks
# above cannot see: HTTPS_PROXY=https://<cloud> silently tunnels a
# loopback-looking request off-machine. A configured proxy is therefore
# checked against the denylist + loopback rule exactly like a provider
# host. (Our own httpx clients additionally pass trust_env=False.)
PROXY_ENV_VARS: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


class LocalOnlyError(RuntimeError):
    """Raised when a non-local URL or telemetry variable is detected."""


def _host_is_local(host: str) -> bool:
    return host.lower() in LOCAL_HOSTS


def _host_is_denylisted(host: str) -> bool:
    # Round-2 audit (F3): normalise a trailing-dot FQDN. ``api.openai.com.``
    # is a valid hostname that resolves identically to ``api.openai.com``
    # but failed both the exact-set lookup and the suffix match. Strip
    # the trailing dot (and any casing) before matching.
    h = host.lower().rstrip(".")
    if h in CLOUD_DENYLIST:
        return True
    return any(h.endswith(suffix) for suffix in CLOUD_DENYLIST_SUFFIXES)


def _assert_env_host_local(var: str, raw: str, *, relax_loopback: bool) -> None:
    """Apply the denylist + loopback rules to a host-bearing env var value.

    Accepts a bare ``host:port`` or a full URL. A denylisted host is
    always rejected; a non-loopback host is rejected unless the loopback
    requirement is relaxed — identical treatment to a Settings URL field.
    """
    value = raw.strip()
    if not value:
        return
    parsed_host = urlparse(value if "://" in value else f"http://{value}").hostname or ""
    if not parsed_host:
        return
    if _host_is_denylisted(parsed_host):
        raise LocalOnlyError(f"{var}={raw!r} matches the cloud provider denylist")
    if not relax_loopback and not _host_is_local(parsed_host):
        raise LocalOnlyError(f"{var}={raw!r} is not a loopback address (host={parsed_host})")


def assert_local_only(settings: Settings, env: dict[str, str] | None = None) -> None:
    # Round-2 audit (F1): the cloud-provider DENYLIST and the telemetry
    # kill-list are UNCONDITIONAL — "no cloud, ever" is a hard rule, not
    # something a single ``LOCAL_ONLY=false`` / ``ALLOW_REMOTE_PROVIDERS=true``
    # env flip can switch off. Pre-fix, either flag returned early and
    # skipped the whole guard, so ``EMBEDDING_BASE_URL=https://api.openai.com``
    # shipped data off-machine. Only the loopback-allow requirement
    # (``_host_is_local``) is relaxable — that exists for the documented
    # case of fronting the service with an on-prem, non-loopback,
    # non-cloud host.
    relax_loopback = (not settings.local_only) or settings.allow_remote_providers

    env_map = env if env is not None else dict(os.environ)

    for name, url in settings.url_fields().items():
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            raise LocalOnlyError(f"{name}={url!r} is missing a host")
        if _host_is_denylisted(host):
            raise LocalOnlyError(f"{name}={url!r} matches the cloud provider denylist")
        if not relax_loopback and not _host_is_local(host):
            raise LocalOnlyError(f"{name}={url!r} is not a loopback address (host={host})")

    # Audit every provider-SDK host env var AND proxy env var the same
    # way as a Settings URL field — see PROVIDER_HOST_ENV_VARS /
    # PROXY_ENV_VARS. A denylisted host is always rejected; a non-loopback
    # one is rejected unless the loopback requirement is relaxed.
    for var in (*PROVIDER_HOST_ENV_VARS, *PROXY_ENV_VARS):
        _assert_env_host_local(var, env_map.get(var) or "", relax_loopback=relax_loopback)

    for var in TELEMETRY_KILL_LIST:
        if env_map.get(var):
            raise LocalOnlyError(f"telemetry / cloud-credential env var is set: {var}")
