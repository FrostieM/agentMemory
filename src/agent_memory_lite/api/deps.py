"""FastAPI dependency providers.

Tests override `get_settings_dep` and `get_db_dep` via `app.dependency_overrides`
to inject a tmp DB and a deterministic settings instance.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends

from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.connection import close_connection, open_connection


def get_settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_db_dep(settings: SettingsDep) -> Iterator[sqlite3.Connection]:
    conn = open_connection(settings.db_path)
    try:
        yield conn
    finally:
        close_connection(conn)


DbDep = Annotated[sqlite3.Connection, Depends(get_db_dep)]
