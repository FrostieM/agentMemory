"""Guard-coverage invariant for write routes (release plan task #115).

Strict workspace isolation has two route-level checks:
- ``ensure_workspace_writable``  -- the target workspace is writable (not foreign).
- ``ensure_workspace_matches_db`` -- the *physical* SQLite connection actually
  belongs to that workspace, so a misrouted ``conn`` can never write rows into
  the wrong workspace's DB.

Every write route already pairs them, but nothing stopped a *future* write route
from gating only the first. This pins the invariant: any route module that uses
the write gate (``ensure_workspace_writable``) must also carry the DB-match
guard (``ensure_workspace_matches_db`` directly, or the ``_ensure_write_routed``
helper that calls both). A new write route that forgets the guard fails here.
"""

from __future__ import annotations

from pathlib import Path

_ROUTES_DIR = Path(__file__).resolve().parents[3] / "src" / "agent_memory_lite" / "api" / "routes"

# Documented exemptions (must stay tiny + justified):
#   evals.py -- runs the eval suite against an ephemeral ``:memory:`` DB, so
#   there is no workspace-routed connection to match.
_EXEMPT: frozenset[str] = frozenset({"evals.py"})


def test_routes_dir_is_discovered() -> None:
    assert _ROUTES_DIR.is_dir(), f"routes dir not found: {_ROUTES_DIR}"
    assert any(_ROUTES_DIR.glob("*.py"))


def test_write_routes_carry_db_match_guard() -> None:
    offenders: list[str] = []
    for path in sorted(_ROUTES_DIR.glob("*.py")):
        if path.name in _EXEMPT:
            continue
        src = path.read_text(encoding="utf-8")
        is_write_route = "ensure_workspace_writable" in src
        has_match_guard = "ensure_workspace_matches_db" in src or "_ensure_write_routed" in src
        if is_write_route and not has_match_guard:
            offenders.append(path.name)
    assert not offenders, (
        "write route(s) gate writability but lack the physical-DB-match guard "
        "(ensure_workspace_matches_db / _ensure_write_routed): " + ", ".join(offenders)
    )
