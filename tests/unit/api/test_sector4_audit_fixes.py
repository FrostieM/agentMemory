"""v3.5 sector-4 audit-followup: API surface hardening.

Three contracts locked:

1. ``X-Memory-DB-Path`` / ``X-Memory-Vector-Path`` headers are
   constrained to paths that appear in the workspace registry.
   Anything else raises ``ValidationError`` → HTTP 400, preventing
   the previous "read any SQLite file on disk" gap that the audit
   flagged as the highest-severity issue in the API layer.

2. The global ``Exception`` handler converts any unhandled exception
   into a typed JSON 500 envelope instead of FastAPI's bare
   ``"Internal Server Error"`` string — and crucially does NOT echo
   the exception message back (it can carry SQL fragments / file
   paths / setup info).

3. Legacy capability-outcome HTTP reporting is no longer an active
   API path; capability outcomes flow through v3 plan-step outcome
   feeding instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_lite.api.deps import (
    _allowed_db_paths,
    _allowed_vector_paths,
    _resolve_db_path,
)
from agent_memory_lite.api.errors import ValidationError


class _FakeRequest:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        from starlette.datastructures import Headers, QueryParams  # noqa: PLC0415

        self.headers = Headers(headers or {})
        self.query_params = QueryParams("")


def test_resolve_db_path_returns_default_when_no_override(tmp_path: Path) -> None:
    """Without the header, the anchor settings.db_path wins (no change
    from pre-audit behaviour)."""
    from agent_memory_lite.config.settings import Settings  # noqa: PLC0415

    db = tmp_path / "memory.db"
    db.write_bytes(b"")
    settings = Settings(
        db_path=db,
        vector_db_path=tmp_path / "vectors.lance",
        workspace_id="default",
    )
    request = _FakeRequest()
    resolved = _resolve_db_path(request, settings)
    assert resolved == settings.db_path


def test_resolve_db_path_rejects_unregistered_override(tmp_path: Path) -> None:
    """v3.5 fix: a header pointing at a file NOT in the registry must
    be rejected with ValidationError — previously the service would
    happily open ``/etc/passwd`` (or any sibling project's DB) and
    serve queries against it."""
    from agent_memory_lite.config.settings import Settings  # noqa: PLC0415

    anchor = tmp_path / "anchor.db"
    anchor.write_bytes(b"")
    foreign = tmp_path / "foreign.db"
    foreign.write_bytes(b"")
    settings = Settings(
        db_path=anchor,
        vector_db_path=tmp_path / "vectors.lance",
        workspace_id="default",
        workspaces_file=tmp_path / "no_registry.json",
    )
    request = _FakeRequest({"x-memory-db-path": str(foreign)})
    with pytest.raises(ValidationError, match="not in the workspace registry"):
        _resolve_db_path(request, settings)


def test_resolve_db_path_accepts_registered_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registered workspace paths still work via the header."""
    from agent_memory_lite.config.settings import Settings  # noqa: PLC0415

    anchor = tmp_path / "anchor.db"
    anchor.write_bytes(b"")
    mate = tmp_path / "mate.db"
    mate.write_bytes(b"")
    registry_path = tmp_path / "workspaces.json"
    import json as _json  # noqa: PLC0415

    registry_path.write_text(
        _json.dumps(
            {
                "workspaces": [
                    {
                        "id": "anchor-ws",
                        "db_path": str(anchor),
                        "vector_path": str(tmp_path / "vectors.lance"),
                    },
                    {
                        "id": "mate-ws",
                        "db_path": str(mate),
                        "vector_path": str(tmp_path / "vectors2.lance"),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    # Settings ignores the constructor arg and reads from env var.
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry_path))
    settings = Settings(
        db_path=anchor,
        vector_db_path=tmp_path / "vectors.lance",
        workspace_id="default",
    )
    request = _FakeRequest({"x-memory-db-path": str(mate)})
    resolved = _resolve_db_path(request, settings)
    assert resolved.resolve() == mate.resolve()


def test_allowed_db_paths_includes_anchor_plus_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_memory_lite.config.settings import Settings  # noqa: PLC0415

    anchor = tmp_path / "anchor.db"
    anchor.write_bytes(b"")
    mate = tmp_path / "mate.db"
    mate.write_bytes(b"")
    registry_path = tmp_path / "workspaces.json"
    import json as _json  # noqa: PLC0415

    registry_path.write_text(
        _json.dumps(
            {
                "workspaces": [
                    {
                        "id": "mate-ws",
                        "db_path": str(mate),
                        "vector_path": str(tmp_path / "v.lance"),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    # Settings reads paths from env via validation_alias.
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry_path))
    monkeypatch.setenv("MEMORY_DB_PATH", str(anchor))
    monkeypatch.setenv("VECTOR_DB_PATH", str(tmp_path / "vectors.lance"))
    settings = Settings(workspace_id="default")
    allowed = _allowed_db_paths(settings)
    assert anchor.resolve() in allowed
    assert mate.resolve() in allowed


def test_allowed_vector_paths_uses_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parallel test for vector-store path validation."""
    from agent_memory_lite.config.settings import Settings  # noqa: PLC0415

    anchor_v = tmp_path / "anchor_vec.lance"
    anchor_v.mkdir()
    mate_v = tmp_path / "mate_vec.lance"
    mate_v.mkdir()
    registry_path = tmp_path / "workspaces.json"
    import json as _json  # noqa: PLC0415

    registry_path.write_text(
        _json.dumps(
            {
                "workspaces": [
                    {
                        "id": "mate-ws",
                        "db_path": str(tmp_path / "mate.db"),
                        "vector_path": str(mate_v),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry_path))
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "anchor.db"))
    monkeypatch.setenv("VECTOR_DB_PATH", str(anchor_v))
    settings = Settings(workspace_id="default")
    allowed = _allowed_vector_paths(settings)
    assert anchor_v.resolve() in allowed
    assert mate_v.resolve() in allowed
