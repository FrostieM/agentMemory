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

    # Round-2 audit (F2): the Ollama client library reads ``OLLAMA_HOST``
    # directly — a process could leave ``llm_base_url`` loopback while
    # ``OLLAMA_HOST=https://api.together.xyz`` silently redirects every
    # LLM call past this guard. Audit it explicitly: a denylisted
    # OLLAMA_HOST is always rejected; a non-loopback one is rejected
    # unless the loopback requirement is relaxed.
    ollama_host_raw = (env_map.get("OLLAMA_HOST") or "").strip()
    if ollama_host_raw:
        # OLLAMA_HOST may be a bare host:port or a full URL.
        parsed_ollama = urlparse(
            ollama_host_raw if "://" in ollama_host_raw else f"http://{ollama_host_raw}"
        )
        ollama_host = parsed_ollama.hostname or ""
        if ollama_host and _host_is_denylisted(ollama_host):
            raise LocalOnlyError(
                f"OLLAMA_HOST={ollama_host_raw!r} matches the cloud provider denylist"
            )
        if ollama_host and not relax_loopback and not _host_is_local(ollama_host):
            raise LocalOnlyError(
                f"OLLAMA_HOST={ollama_host_raw!r} is not a loopback address (host={ollama_host})"
            )

    for var in TELEMETRY_KILL_LIST:
        if env_map.get(var):
            raise LocalOnlyError(f"telemetry / cloud-credential env var is set: {var}")
