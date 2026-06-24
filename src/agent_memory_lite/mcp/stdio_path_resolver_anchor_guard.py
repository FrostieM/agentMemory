"""Startup anchor-consistency guard for the MCP stdio runtime.

Lifted out of ``stdio_path_resolver`` so that module stays under the
150-SLOC ceiling. Holds the fail-closed ``assert_anchor_consistent`` check
plus the ``_paths_equivalent`` primitive it shares with the resolver, which
is re-exported from ``stdio_path_resolver`` for the resolver's own use.
"""

from __future__ import annotations

from pathlib import Path

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry


def _paths_equivalent(a: Path, b: Path) -> bool:
    """True when ``a`` and ``b`` name the same path.

    ``os.path.samefile`` (device + inode) sees through symlinks, junctions,
    ``subst`` drives and UNC/drive-letter skew that a plain string compare
    misses. It needs both paths to exist, so fall back to a RESOLVED compare
    when one does not -- a stale / not-yet-created path, or a relative registry
    ``db_path`` vs an absolute settings path, where a raw string compare would
    wrongly differ. Mirrors ``workspace_paths._connections_match``; the final
    casefold compare is a last resort if ``resolve()`` itself raises.
    """
    import os  # noqa: PLC0415

    try:
        return os.path.samefile(a, b)
    except OSError:
        pass
    try:
        return a.resolve() == b.resolve()
    except (OSError, ValueError):
        return str(a).rstrip("\\/").casefold() == str(b).rstrip("\\/").casefold()


def assert_anchor_consistent(settings: Settings) -> None:
    """Fail closed at startup when the anchor's DB belongs to another workspace.

    Two mis-anchor shapes are rejected before serving (the '2026-05 server stuck
    on the wrong anchor' class, where a pinned ``MEMORY_DB_PATH`` / inherited
    ``.mcp.json`` env aims the process at the wrong DB):

    * the anchor ``workspace_id`` IS registered but ``db_path`` is not that
      workspace's registered DB; and
    * the anchor ``workspace_id`` is NOT registered yet sits on the physical DB
      that belongs to some OTHER registered workspace.

    Scope limits (intentional): an unregistered anchor on an unregistered DB is
    allowed -- a fresh / local-only workspace, nothing authoritative to
    contradict. The auto-hub branch that adopts ``entries[0]`` sets
    ``workspace_id`` and ``db_path`` from the SAME entry, so it is consistent by
    construction and this guard cannot (and need not) flag it. A missing or
    unreadable registry fails OPEN -- there is nothing to check against, and the
    per-DB manifest guard remains the second backstop.
    """
    # A 'default' anchor under forbid_default_workspace is always a
    # misconfiguration: the fallback workspace was forbidden yet became the
    # anchor (a bare / unregistered cwd). Fail closed HERE, at startup, with a
    # clear message -- otherwise the process serves and dies opaquely on the
    # first DB-touching tool call with a manifest error.
    if settings.workspace_id == "default" and settings.forbid_default_workspace:
        raise ValueError(
            "MCP anchor mis-configured: resolved to the 'default' workspace but "
            "MEMORY_FORBID_DEFAULT_WORKSPACE is set. Open a chat in a registered "
            "project directory, or set MEMORY_WORKSPACE_ID (and MEMORY_HUB_MODE "
            "for a cross-project hub); refusing to serve a forbidden default "
            "workspace."
        )
    try:
        entries = WorkspaceRegistry(settings.workspaces_file).list()
    except Exception:
        return
    db_path = Path(settings.db_path)
    entry = next((e for e in entries if e.id == settings.workspace_id), None)
    if entry is not None and entry.db_path:
        if _paths_equivalent(Path(entry.db_path), db_path):
            return
        raise ValueError(
            f"MCP anchor mis-configured: workspace_id={settings.workspace_id!r} "
            f"is registered with db_path={entry.db_path!r} but the server "
            f"resolved db_path={str(settings.db_path)!r}. Fix MEMORY_DB_PATH / "
            "the .mcp.json env, or re-register the workspace if its DB moved; "
            "refusing to serve a mis-anchored database."
        )
    # Anchor id is unregistered: it must still not sit on another workspace's DB.
    foreign = next(
        (e for e in entries if e.db_path and _paths_equivalent(Path(e.db_path), db_path)),
        None,
    )
    if foreign is not None:
        raise ValueError(
            f"MCP anchor mis-configured: workspace_id={settings.workspace_id!r} "
            f"is not registered but its db_path={str(settings.db_path)!r} is "
            f"workspace {foreign.id!r}'s registered database. Set "
            f"MEMORY_WORKSPACE_ID={foreign.id!r}, or point MEMORY_DB_PATH at this "
            "workspace's own DB; refusing to serve a mis-anchored database."
        )
