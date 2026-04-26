"""Entry point for `python -m agent_memory_lite`.

Binds FastAPI to 127.0.0.1 only. The host is hard-coded; the port is taken from
settings. Local-only enforcement runs inside `create_app`.
"""

from __future__ import annotations

import uvicorn

from agent_memory_lite.api.app import create_app
from agent_memory_lite.config.settings import get_settings
from agent_memory_lite.logging_setup import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = create_app(settings)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
