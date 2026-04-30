"""Optional local HTTP API token guard."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from secrets import compare_digest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from agent_memory_lite.config.settings import Settings

CallNext = Callable[[Request], Awaitable[Response]]


def _resolve_api_token_file(settings: Settings) -> Path:
    path = settings.api_token_file
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == ".agent_memory":
        return settings.db_path.parent / Path(*path.parts[1:])
    return path


def _load_api_token(settings: Settings) -> str:
    token_file = _resolve_api_token_file(settings)
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        msg = f"MEMORY_REQUIRE_API_TOKEN is true but token file is missing: {token_file}"
        raise RuntimeError(msg) from exc
    if not token:
        msg = f"MEMORY_REQUIRE_API_TOKEN is true but token file is empty: {token_file}"
        raise RuntimeError(msg)
    return token


def _extract_bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def install_api_token_guard(app: FastAPI, settings: Settings) -> None:
    """Require a local bearer token for /memory/* when explicitly enabled."""
    if not settings.require_api_token:
        return

    expected_token = _load_api_token(settings)

    @app.middleware("http")
    async def _require_memory_api_token(request: Request, call_next: CallNext) -> Response:
        if not request.url.path.startswith("/memory/"):
            return await call_next(request)

        supplied_token = _extract_bearer_token(request.headers.get("authorization"))
        if supplied_token is None or not compare_digest(supplied_token, expected_token):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "detail": "Missing or invalid API token.",
                },
            )
        return await call_next(request)
