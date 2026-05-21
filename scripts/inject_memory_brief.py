"""UserPromptSubmit memory brief hook: prepend a ≤500-token brief from /memory/brief.

Memory brief hook -- replacement for ``scripts/inject_memory_context.py``. Where the v2 hook
emits a verbose ``<memory_context>`` envelope (~1500 tokens) by calling
``/memory/get_context``, this hook emits a tight pre-task brief composed
from compact projections (~500 tokens) by calling ``/memory/brief``.

Architecture:

* The v2 hook keeps shipping for projects still on v2.
* This hook is opt-in via setup_agent.py (added at week 5)
  or by manually editing ``~/.claude/settings.json``.
* At week 8 cutover, setup_agent flips new projects to this hook by
  default.

Stdin: a Claude Code event JSON with at least ``{"prompt": "<text>"}``.
Other fields (``cwd``, ``session_id``, ``workspace_id``) are forwarded
into workspace resolution when present.

Stdout: a single block

    <agent-memory>
    <memory_brief>
      ...500-token body_md...
    </memory_brief>
    </agent-memory>

Failure-soft: any HTTP / JSON / FS error emits a one-line notice and exits
0 so the agent still sees the user's prompt.

v3.2 once-per-session dedup (live-2026-05-20 audit feedback): when the
caller passes a stable ``session_id`` AND the body_md hash matches what
the same session received last turn, emit a 1-line "memory unchanged"
notice instead of the full brief. Saves ~400 input tokens / turn across
follow-up turns. The full brief re-renders every
``MEMORY_BRIEF_REFRESH_EVERY_N_TURNS`` turns (default 20) so the agent
never goes a long session without a refresh anchor. Set
``MEMORY_BRIEF_DEDUP_ENABLED=false`` to disable; the legacy
emit-every-turn behavior comes back.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

# Force UTF-8 stdout so the brief block is never truncated on Windows.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
with contextlib.suppress(AttributeError, ValueError):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _validate_base(raw: str) -> str:
    """v3.5 sector-6+7 audit-followup: refuse non-loopback AGENT_MEMORY_BASE.

    Pre-fix: any process that could set the env var redirected every
    prompt + brief request to an attacker host (response gets injected
    verbatim into the agent's context). Loopback-only by default keeps
    the local-only invariant. Operator can opt out with
    ``AGENT_MEMORY_ALLOW_REMOTE_BASE=1`` for the rare case of an SSH
    tunnel / explicit reverse proxy.
    """
    from urllib.parse import urlparse  # noqa: PLC0415

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return raw
    if os.environ.get("AGENT_MEMORY_ALLOW_REMOTE_BASE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return raw
    print(
        f"# inject_memory_brief: refusing non-loopback AGENT_MEMORY_BASE={raw!r} "
        "(set AGENT_MEMORY_ALLOW_REMOTE_BASE=1 to override). "
        "Falling back to http://127.0.0.1:8765.",
        file=sys.stderr,
    )
    return "http://127.0.0.1:8765"


DEFAULT_BASE = _validate_base(os.environ.get("AGENT_MEMORY_BASE", "http://127.0.0.1:8765"))
DEFAULT_WORKSPACE = os.environ.get("AGENT_MEMORY_WORKSPACE", "")
DEFAULT_MAX_TOKENS = int(os.environ.get("AGENT_MEMORY_BRIEF_TOKENS", "500"))
DEFAULT_TIMEOUT = float(os.environ.get("AGENT_MEMORY_BRIEF_TIMEOUT", "10.0"))
REGISTRY_PATH = (
    Path(os.environ["MEMORY_WORKSPACES_FILE"])
    if os.environ.get("MEMORY_WORKSPACES_FILE")
    else Path.home() / ".agent_memory" / "workspaces.json"
)
# Fallback workspace when cwd is not inside any registered project_root.
# Mirrors the v2 inject_memory_context.py global-fallback behaviour so the
# operator who opens Claude Code from a parent / unregistered directory
# still gets a meaningful brief (pinned rules from the global workspace)
# instead of an empty envelope. Disable with
# ``AGENT_MEMORY_HOOK_FALLBACK=disabled``.
GLOBAL_FALLBACK_WORKSPACE = os.environ.get("AGENT_MEMORY_FALLBACK_WORKSPACE", "global")
HOOK_FALLBACK_DISABLED = os.environ.get("AGENT_MEMORY_HOOK_FALLBACK", "").strip().lower() in {
    "0",
    "false",
    "no",
    "disabled",
    "off",
}


def _read_event() -> dict[str, object]:
    """Read the JSON event Claude Code writes to stdin. Tolerates empty stdin."""
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _list_registry() -> list[dict[str, object]]:
    if not REGISTRY_PATH.exists():
        return []
    try:
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    entries = payload.get("workspaces")
    return entries if isinstance(entries, list) else []


def _safe_registry_path(raw: str) -> str:
    """Round-2 audit: reject registry db_path / vector_path shapes that
    are never a legitimate local DB path — UNC / network paths, null
    bytes, non-absolute paths. The registry file is the trust root, so
    full root-confinement buys little and would break the multi-project
    design; this only blocks the unambiguously-malicious shapes. A
    rejected value returns '' → the hook routes without the header and
    the service falls back to its anchor DB."""
    raw = (raw or "").strip()
    if not raw or "\x00" in raw:
        return ""
    if raw.startswith(("\\\\", "//")):  # UNC / network share
        return ""
    if not os.path.isabs(raw):
        return ""
    return raw


def _resolve_workspace_from_cwd(cwd: Path) -> tuple[str, str, str]:
    """Walk cwd parents; return (workspace_id, db_path, vector_path) on hit, else empty strings."""
    entries = _list_registry()
    if not entries:
        return ("", "", "")
    candidate = cwd.resolve()
    for parent in [candidate, *candidate.parents]:
        target = str(parent).rstrip("\\/").casefold()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            root = str(entry.get("project_root", "")).rstrip("\\/").casefold()
            if root and root == target:
                return (
                    str(entry.get("id", "")),
                    _safe_registry_path(str(entry.get("db_path", ""))),
                    _safe_registry_path(str(entry.get("vector_path", ""))),
                )
    return ("", "", "")


def _emit_brief(body_md: str) -> None:
    sys.stdout.write("<agent-memory>\n<memory_brief>\n")
    sys.stdout.write(body_md)
    if not body_md.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.write("</memory_brief>\n</agent-memory>\n")


def _emit_notice(message: str) -> None:
    sys.stdout.write(
        f"<agent-memory>\n<!-- memory brief hook notice: {message} -->\n</agent-memory>\n"
    )


def _emit_unchanged(turn: int) -> None:
    """v3.2: a 1-line breadcrumb instead of the full ~500-token brief when
    memory state has not changed since the previous turn in this session.
    Carries the turn counter so the operator can see the dedup is firing."""
    sys.stdout.write(
        f"<agent-memory>\n"
        f"<!-- memory brief unchanged since last turn "
        f"(turn={turn}); skipping inline re-render. "
        f"Call memory_brief tool if you need the full body. -->\n"
        f"</agent-memory>\n"
    )


# ---- v3.2 once-per-session dedup memo -------------------------------------

DEFAULT_DEDUP_ENABLED = os.environ.get("MEMORY_BRIEF_DEDUP_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEFAULT_REFRESH_EVERY_N = int(os.environ.get("MEMORY_BRIEF_REFRESH_EVERY_N_TURNS", "20"))
SESSIONS_DIR = Path(
    os.environ.get("MEMORY_BRIEF_SESSIONS_DIR", str(Path.home() / ".agent_memory" / "sessions"))
)


def _session_memo_path(session_id: str) -> Path:
    """Per-session memo file. Slug the id to keep it filesystem-safe."""
    slug = hashlib.sha1(session_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    return SESSIONS_DIR / f"brief_{slug}.json"


def _load_memo(session_id: str) -> dict[str, object]:
    """Read the prior memo for this session. ``{}`` on miss / IO error."""
    p = _session_memo_path(session_id)
    if not p.exists():
        return {}
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_memo(session_id: str, *, body_hash: str, turn: int) -> None:
    """Best-effort write — failure here must not break the hook."""
    p = _session_memo_path(session_id)
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"hash": body_hash, "turn": turn}),
            encoding="utf-8",
        )
    except OSError:
        return


def _body_hash(body_md: str) -> str:
    """Stable fingerprint of the brief body — short SHA1 hex."""
    return hashlib.sha1(body_md.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _should_skip_brief(
    *,
    session_id: str | None,
    body_md: str,
) -> tuple[bool, int]:
    """Return ``(skip, turn_counter)``.

    ``skip=True`` when dedup is enabled, the session memo says we already
    sent the same body within the refresh window, AND the workspace state
    did not change. Otherwise ``skip=False`` and the caller must emit
    the full brief + persist the memo with the new turn count.
    """
    if not DEFAULT_DEDUP_ENABLED or not session_id:
        return (False, 0)
    memo = _load_memo(session_id)
    prior_hash = str(memo.get("hash") or "")
    raw_turn = memo.get("turn")
    prior_turn = int(raw_turn) if isinstance(raw_turn, (int, str)) and str(raw_turn).strip() else 0
    current_hash = _body_hash(body_md)
    next_turn = prior_turn + 1
    if prior_hash == current_hash and next_turn < DEFAULT_REFRESH_EVERY_N:
        # Same content + we're not at the periodic refresh point.
        _save_memo(session_id, body_hash=current_hash, turn=next_turn)
        return (True, next_turn)
    # Hash changed OR refresh due → emit + reset counter.
    _save_memo(session_id, body_hash=current_hash, turn=1)
    return (False, 1)


def _fetch_brief(
    *,
    base_url: str,
    workspace_id: str,
    max_tokens: int,
    headers: dict[str, str],
    session_id: str | None = None,
) -> dict[str, object] | None:
    """Call /memory/brief. Returns the envelope ``data`` payload or None on error.

    Passes ``session_id`` when present so the service can apply sticky-
    brief (shrink ``max_tokens`` on follow-up calls in the same session).
    Service ignores the param when unknown, so this stays compatible
    with older HTTP versions.
    """
    params: dict[str, str | int] = {"workspace_id": workspace_id, "max_tokens": max_tokens}
    if session_id:
        params["session_id"] = session_id
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/memory/brief",
            params=params,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        body = response.json()
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict) or not body.get("ok"):
        return None
    data = body.get("data")
    return data if isinstance(data, dict) else None


def main() -> int:  # noqa: PLR0912 — linear hook flow with explicit early returns per failure mode
    event = _read_event()
    prompt = str(event.get("prompt", "")).strip()
    if not prompt:
        return 0

    workspace = str(event.get("workspace_id") or DEFAULT_WORKSPACE or "")
    db_path = os.environ.get("AGENT_MEMORY_DB_PATH", "")
    vector_path = os.environ.get("AGENT_MEMORY_VECTOR_PATH", "")

    if not workspace or not db_path:
        cwd_candidates: list[Path] = []
        event_cwd = event.get("cwd")
        if event_cwd:
            cwd_candidates.append(Path(str(event_cwd)))
        with contextlib.suppress(OSError):
            cwd_candidates.append(Path(os.getcwd()))
        for candidate in cwd_candidates:
            ws, db, vec = _resolve_workspace_from_cwd(candidate)
            if ws:
                workspace = workspace or ws
                db_path = db_path or db
                vector_path = vector_path or vec
                break

    used_global_fallback = False
    if not workspace:
        if HOOK_FALLBACK_DISABLED:
            _emit_notice(
                "no workspace registered for this cwd. "
                "Run `python scripts/setup_agent.py --project <path>` "
                "from the agent-memory-lite repo, or unset "
                "AGENT_MEMORY_HOOK_FALLBACK to use the `global` workspace."
            )
            return 0
        # Fall back to the global workspace — operator opened Claude Code
        # from a directory that doesn't match any registered project_root.
        # The global workspace is seeded with the same 3 pinned discipline
        # rules so the brief is still useful (not the empty envelope that
        # blocked v1.x users from seeing anything at all).
        workspace = GLOBAL_FALLBACK_WORKSPACE
        # Look up the global workspace's db_path / vector_path in the
        # registry so the HTTP service routes there via X-Memory-DB-Path.
        for entry in _list_registry():
            if isinstance(entry, dict) and entry.get("id") == workspace:
                db_path = db_path or _safe_registry_path(str(entry.get("db_path") or ""))
                vector_path = vector_path or _safe_registry_path(
                    str(entry.get("vector_path") or "")
                )
                break
        used_global_fallback = True

    headers: dict[str, str] = {}
    if db_path:
        headers["X-Memory-DB-Path"] = db_path
    if vector_path:
        headers["X-Memory-Vector-Path"] = vector_path

    raw_session = event.get("session_id") or event.get("transcript_path") or ""
    session_id = str(raw_session) if raw_session else None
    # v3.2: skip the server-side sticky-brief shrink so the body stays
    # consistent across calls — otherwise our fingerprint dedup misses
    # the 2nd-turn emit when server shrinks 500->200 tokens. Client-side
    # dedup gives the same savings without the extra mid-turn render.
    data = _fetch_brief(
        base_url=DEFAULT_BASE,
        workspace_id=workspace,
        max_tokens=DEFAULT_MAX_TOKENS,
        headers=headers,
        session_id=None if DEFAULT_DEDUP_ENABLED else session_id,
    )
    if data is None:
        _emit_notice(
            f"agent-memory-lite memory brief unreachable at {DEFAULT_BASE}. "
            "Run `python -m agent_memory_lite` to start the service."
        )
        return 0

    body_md = data.get("body_md")
    if not isinstance(body_md, str) or not body_md.strip():
        _emit_notice("memory brief returned no body — empty workspace?")
        return 0

    if used_global_fallback:
        # Prefix the brief with a visible breadcrumb so the agent (and
        # operator) see that the brief is from the global workspace, not
        # the project they meant to open. Mirrors v2 inject_memory_context.py
        # hook_notice for parity.
        registered = sorted(
            str(e.get("id", "?")) for e in _list_registry() if isinstance(e, dict)
        ) or ["<empty>"]
        notice = (
            f'<hook_notice severity="info" code="global_fallback">\n'
            f"  memory brief hook resolved no project workspace for cwd; using "
            f"`{workspace}` (registered workspaces: {registered}). "
            f"To get a project-scoped brief, open Claude Code from one of the "
            f"registered project roots, or set "
            f"AGENT_MEMORY_WORKSPACE=<id> in the env.\n"
            f"</hook_notice>\n"
        )
        body_md = notice + body_md

    # v3.2 once-per-session dedup: skip the full body if the same brief
    # was sent earlier in this session AND we haven't hit the periodic
    # refresh point. Saves ~500 tokens of input on every follow-up turn
    # where memory state did not change.
    skip, turn = _should_skip_brief(session_id=session_id, body_md=body_md)
    if skip:
        _emit_unchanged(turn)
        return 0

    _emit_brief(body_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
