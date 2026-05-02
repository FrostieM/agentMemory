"""Tiny env-var helpers used across the MCP stdio modules.

Pulled out of ``stdio_runtime.py`` so the runtime can stay focused on
the connection cache + path resolution.
"""

from __future__ import annotations

import os

from agent_memory_lite.config.settings import Settings


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, *, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _memory_http_base_url(settings: Settings) -> str:
    raw = os.environ.get("MEMORY_HTTP_BASE_URL") or os.environ.get("AGENT_MEMORY_HTTP_BASE_URL")
    if raw:
        return raw.rstrip("/")
    return f"http://127.0.0.1:{settings.api_port}"
