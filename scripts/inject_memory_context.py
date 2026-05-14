"""Claude Code UserPromptSubmit hook: prepend agent-memory-lite context.

Wired into Claude Code's hook system, this runs before each user message
reaches the agent. It calls /memory/get_context (or the in-process MCP
service), wraps the response in a plain-text preamble, and emits stdout
that Claude Code injects ahead of the user's prompt.

Stdin: a JSON object emitted by Claude Code with at least
    {"prompt": "<user text>"}
Other Claude Code fields (session_id, cwd, etc.) are tolerated and
forwarded into the memory query when present.

Stdout: a single block

    <agent-memory>
    <memory_context>
      ...
    </memory_context>
    </agent-memory>

This block is what Claude Code prepends to the user's message. The block
is intentionally token-bounded (default 1500 tokens of memory). On any
failure (service down, JSON malformed, network error) the hook prints a
short notice and exits 0 so the user's prompt still reaches the agent.

Configure in `~/.claude/settings.json`:

    {
      "hooks": {
        "UserPromptSubmit": [{
          "hooks": [{
            "type": "command",
            "command": "<venv-python> <repo>/scripts/inject_memory_context.py"
          }]
        }]
      }
    }

`scripts/setup_agent.py` writes that snippet for you.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

# Force UTF-8 stdout/stderr regardless of the host console codepage.
# Windows defaults to cp1251/cp1252 which raises ``UnicodeEncodeError`` on
# any common non-ASCII character (em-dash, arrows, Cyrillic) and silently
# truncates the envelope at the first bad byte. Reconfigure here so the
# hook output is always UTF-8 even when Claude Code spawns the subprocess
# without ``PYTHONIOENCODING=utf-8`` in the env.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
with contextlib.suppress(AttributeError, ValueError):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

DEFAULT_BASE = os.environ.get("AGENT_MEMORY_BASE", "http://127.0.0.1:8765")
DEFAULT_WORKSPACE = os.environ.get("AGENT_MEMORY_WORKSPACE", "default")
DEFAULT_MAX_TOKENS = int(os.environ.get("AGENT_MEMORY_INJECT_TOKENS", "1500"))
DEFAULT_TIMEOUT = float(os.environ.get("AGENT_MEMORY_INJECT_TIMEOUT", "30.0"))
DEFAULT_DEDUPE_TTL_SECONDS = float(os.environ.get("AGENT_MEMORY_HOOK_DEDUPE_TTL", "2.0"))

# v1.10 correction capture — env defaults mirror Settings defaults.
# Reading via os.environ keeps the hook independent of pydantic Settings
# import latency. The Python flag is true unless explicitly set to a
# falsy value, matching agent_memory_lite/config/settings.py defaults.
_CORRECTION_TRUTHY = {"1", "true", "yes", "on"}
_CORRECTION_FALSY = {"0", "false", "no", "off", "disabled"}


def _flag_on(env_name: str, default: bool) -> bool:
    raw = os.environ.get(env_name, "").strip().lower()
    if raw in _CORRECTION_TRUTHY:
        return True
    if raw in _CORRECTION_FALSY:
        return False
    return default


CORRECTION_DETECT_ENABLED = _flag_on("MEMORY_CORRECTION_DETECT_ENABLED", True)
CORRECTION_TRANSCRIPT_READ_ENABLED = _flag_on("MEMORY_CORRECTION_TRANSCRIPT_READ_ENABLED", True)
CORRECTION_MIN_USER_LEN = int(os.environ.get("MEMORY_CORRECTION_MIN_USER_LEN", "30"))
CORRECTION_MIN_AGENT_LEN = int(os.environ.get("MEMORY_CORRECTION_MIN_AGENT_LEN", "50"))
CORRECTION_MIN_CONFIDENCE = float(os.environ.get("MEMORY_CORRECTION_MIN_CONFIDENCE", "0.5"))
CORRECTION_PAIR_WINDOW_MIN = int(os.environ.get("MEMORY_CORRECTION_PAIR_WINDOW_MIN", "30"))
DEFAULT_REGISTRY = (
    Path(os.environ["MEMORY_WORKSPACES_FILE"])
    if os.environ.get("MEMORY_WORKSPACES_FILE")
    else Path.home() / ".agent_memory" / "workspaces.json"
)
# Global fallback workspace used when the current cwd is not registered
# in the hub registry. Auto-created on first use so a chat opened in any
# directory still gets memory context instead of a "no workspace
# registered" notice. Opt out with AGENT_MEMORY_HOOK_FALLBACK=disabled
# for strict project-mode setups that don't want the global catch-all.
GLOBAL_FALLBACK_WORKSPACE = os.environ.get("AGENT_MEMORY_FALLBACK_WORKSPACE", "global")
GLOBAL_FALLBACK_DIR = (
    Path(os.environ["AGENT_MEMORY_FALLBACK_DIR"])
    if os.environ.get("AGENT_MEMORY_FALLBACK_DIR")
    else Path.home() / ".agent_memory" / "global"
)
HOOK_FALLBACK_DISABLED = os.environ.get("AGENT_MEMORY_HOOK_FALLBACK", "").strip().lower() in {
    "0",
    "false",
    "no",
    "disabled",
    "off",
}


def _list_registry_entries() -> list[dict[str, object]]:
    """Return the raw ``workspaces`` list from the hub registry, or [].

    Tolerates a missing/corrupt registry file -- the caller wants a best-
    effort breadcrumb (e.g. ``<hook_notice>`` body), not a hard error.
    """
    if not DEFAULT_REGISTRY.exists():
        return []
    try:
        payload = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("workspaces") if isinstance(payload, dict) else None
    return entries if isinstance(entries, list) else []


def _resolve_from_registry(cwd: Path) -> dict[str, str] | None:
    """Walk up from cwd; if a parent matches a registered project_root, return it.

    Returns a dict with `workspace_id`, `db_path`, `vector_path` ready for
    the hook payload. Lets a single global hook auto-route to the right
    project memory without any per-project --workspace arg.
    """
    entries = _list_registry_entries()
    if not entries:
        return None
    candidate = cwd.resolve()
    for parent in [candidate, *candidate.parents]:
        target = str(parent).rstrip("\\/")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            project_root = str(entry.get("project_root", "")).rstrip("\\/")
            if project_root and project_root.casefold() == target.casefold():
                return {
                    "workspace_id": str(entry.get("id", "")),
                    "db_path": str(entry.get("db_path", "")),
                    "vector_path": str(entry.get("vector_path", "")),
                }
    return None


def _ensure_global_fallback() -> dict[str, str] | None:
    """Bootstrap and register the global fallback workspace on first use.

    Returns ``{"workspace_id", "db_path", "vector_path"}`` ready to feed
    into the hook payload. Returns ``None`` if bootstrapping failed
    (the caller falls back to the legacy notice). Subsequent calls hit
    the fast path: directories already exist, registry entry already
    present.
    """
    db_path = GLOBAL_FALLBACK_DIR / "memory.db"
    vector_path = GLOBAL_FALLBACK_DIR / "vectors.lance"
    try:
        GLOBAL_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        if not db_path.exists():
            # Defer the heavy import until we actually need to bootstrap.
            # In steady state (db already there) the hook never imports
            # the agent_memory_lite package and stays sub-second.
            from agent_memory_lite.db.connection import (  # noqa: PLC0415
                close_connection,
                open_connection,
            )
            from agent_memory_lite.db.migrations import (  # noqa: PLC0415
                MIGRATION_DIR,
                apply_migrations,
            )
            from agent_memory_lite.repositories.workspace_manifest_repo import (  # noqa: PLC0415
                ensure_workspace_manifest,
            )

            conn = open_connection(db_path)
            try:
                apply_migrations(conn, MIGRATION_DIR)
                ensure_workspace_manifest(
                    conn,
                    workspace_id=GLOBAL_FALLBACK_WORKSPACE,
                    allow_default_workspace=False,
                )
            finally:
                close_connection(conn)
    except Exception:
        # Any bootstrap failure (missing imports, permission denied,
        # etc.) means we cannot offer a fallback. Caller emits the
        # legacy notice instead so the user sees an actionable error.
        return None

    _ensure_registry_entry(
        workspace_id=GLOBAL_FALLBACK_WORKSPACE,
        db_path=db_path,
        vector_path=vector_path,
    )
    return {
        "workspace_id": GLOBAL_FALLBACK_WORKSPACE,
        "db_path": str(db_path),
        "vector_path": str(vector_path),
    }


def _ensure_registry_entry(*, workspace_id: str, db_path: Path, vector_path: Path) -> None:
    """Idempotently add the global fallback to ``workspaces.json``.

    Uses plain JSON read/modify/write to avoid importing the registry
    module (which pulls heavy deps). Race-safe enough for the
    single-user local case: worst case is two near-simultaneous hook
    invocations both writing the same entry.
    """
    try:
        if DEFAULT_REGISTRY.exists():
            payload = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
        else:
            payload = {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    workspaces = payload.get("workspaces")
    if not isinstance(workspaces, list):
        workspaces = []
        payload["workspaces"] = workspaces
    for entry in workspaces:
        if isinstance(entry, dict) and entry.get("id") == workspace_id:
            return
    workspaces.append(
        {
            "id": workspace_id,
            "db_path": str(db_path),
            "vector_path": str(vector_path),
            "label": "Global fallback (auto-created by hook)",
            "project_root": str(GLOBAL_FALLBACK_DIR),
            "extra": {"created_by": "inject_memory_context_hook"},
        }
    )
    try:
        DEFAULT_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_REGISTRY.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return


def _maybe_capture_correction(
    *,
    event: dict[str, object],
    workspace: str,
    db_path: str | None,
    vector_path: str | None,
    user_prompt: str,
) -> None:
    """Best-effort: detect agent-claim-corrected-by-user pattern and ingest pair.

    Read the Claude Code transcript JSONL referenced by ``event``,
    locate the most recent assistant text turn within
    ``MEMORY_CORRECTION_PAIR_WINDOW_MIN`` minutes, run the correction
    heuristic against (claim, current_prompt). On match, POST two
    `/memory/ingest_episode` calls — first the agent claim, then the
    user correction with `metadata.correction_target_episode_id` set
    so the server-side CorrectionExtractor can pair them.

    Failures (missing transcript_path, parse error, network, server
    rejection) are silently swallowed; the hook continues to its
    normal context-injection path.
    """
    transcript_path = event.get("transcript_path")
    if not transcript_path:
        return
    if len(user_prompt) < CORRECTION_MIN_USER_LEN:
        return

    # Lazy import — module is in scripts/, sibling to this hook.
    try:
        from transcript_pair_extractor import find_last_assistant_text  # noqa: PLC0415
    except ImportError:
        try:
            from scripts.transcript_pair_extractor import (  # noqa: PLC0415
                find_last_assistant_text,
            )
        except ImportError:
            return

    turn = find_last_assistant_text(str(transcript_path), window_minutes=CORRECTION_PAIR_WINDOW_MIN)
    if turn is None or len(turn.text) < CORRECTION_MIN_AGENT_LEN:
        return

    # Lazy import correction patterns from the package — costs ~30ms
    # cold but only when there's actually a recent assistant turn to
    # check, which is rare for non-conversational prompts.
    try:
        from agent_memory_lite.extraction.correction_patterns import (  # noqa: PLC0415
            match_correction,
        )
    except ImportError:
        return

    match = match_correction(
        user_prompt,
        min_user_len=CORRECTION_MIN_USER_LEN,
        min_confidence=CORRECTION_MIN_CONFIDENCE,
    )
    if not match.matched:
        return

    headers: dict[str, str] = {}
    if db_path:
        headers["X-Memory-DB-Path"] = db_path
    if vector_path:
        headers["X-Memory-Vector-Path"] = vector_path
    session_id = str(event.get("session_id") or "")

    # Step 1: ingest the agent claim.
    # We set both the legacy ``metadata.kind`` (for any pre-1.10 reader)
    # AND the namespaced ``metadata.correction_role`` so v1.10's
    # CorrectionExtractor can disambiguate "this episode is a v1.10
    # correction-pair claim" from any other future use of metadata.kind.
    claim_payload = {
        "workspace_id": workspace,
        "session_id": session_id or None,
        "source_type": "agent_action",
        "raw_text": turn.text,
        "trust_level": "agent_observed",
        "importance": 0.5,
        "metadata": {
            "kind": "correction_target",
            "correction_role": "claim",
            "claude_code_uuid": turn.uuid,
            "claude_code_timestamp": turn.timestamp,
        },
    }
    try:
        resp = httpx.post(
            f"{DEFAULT_BASE}/memory/ingest_episode",
            json=claim_payload,
            headers=headers,
            timeout=5.0,
        )
    except (httpx.ConnectError, httpx.HTTPError):
        return
    if resp.status_code >= 400:
        return
    try:
        claim_episode_id = str(resp.json().get("episode_id") or "")
    except (json.JSONDecodeError, ValueError):
        return
    if not claim_episode_id:
        return

    # Step 2: ingest the user correction with cross-reference.
    correction_payload = {
        "workspace_id": workspace,
        "session_id": session_id or None,
        "source_type": "user_message",
        "raw_text": user_prompt,
        "trust_level": "user_asserted",
        "importance": match.confidence,
        "metadata": {
            "kind": "user_correction",
            "correction_role": "user_correction",
            "correction_target_episode_id": claim_episode_id,
            "matched_opener": match.opener_match,
            "matched_body": match.body_match,
            "match_confidence": match.confidence,
        },
    }
    with contextlib.suppress(httpx.ConnectError, httpx.HTTPError):
        httpx.post(
            f"{DEFAULT_BASE}/memory/ingest_episode",
            json=correction_payload,
            headers=headers,
            timeout=5.0,
        )


def _read_event() -> dict[str, object]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"prompt": raw}
    return parsed if isinstance(parsed, dict) else {"prompt": str(parsed)}


def _maybe_emit_memory_audit(*, event: dict[str, object]) -> None:
    """Emit a system-reminder audit prompt when the agent skipped memory writes.

    Best-effort: reads the Claude Code transcript JSONL tail, classifies
    tool_use blocks in the previous assistant turn, and if the agent did
    real file work (Edit/Write/Bash) without any memory write, emits a
    forced-language ``<system-reminder>`` block to stdout. The block lands
    in the agent's context for the new turn AHEAD of the ``<agent-memory>``
    block, promoting the memory-discipline reminder from background
    behavior_instructions to foreground system context — which Claude
    follows reliably.

    Silent on any error (missing transcript, parse error, import failure)
    so the hook degrades to passthrough. Bypass via env
    ``MEMORY_SKIP_AUDIT_PROMPT=1``.
    """
    if os.environ.get("MEMORY_SKIP_AUDIT_PROMPT") == "1":
        return
    transcript_path = event.get("transcript_path")
    if not transcript_path:
        return
    try:
        from agent_memory_lite.extraction.memory_audit_prompt import (  # noqa: PLC0415
            analyze_last_assistant_turn,
            decide_audit,
        )
    except ImportError:
        return
    # Path-traversal defense: only read transcripts under whitelisted roots.
    try:
        from transcript_pair_extractor import _is_under_allowed_root  # noqa: PLC0415
    except ImportError:
        try:
            from scripts.transcript_pair_extractor import (  # noqa: PLC0415
                _is_under_allowed_root,
            )
        except ImportError:
            return
    p = Path(str(transcript_path))
    if not _is_under_allowed_root(p):
        return
    try:
        stats = analyze_last_assistant_turn(p)
        decision = decide_audit(stats)
    except (OSError, ValueError, json.JSONDecodeError):
        return
    if not decision.inject:
        return
    sys.stdout.write(f"<system-reminder>\n{decision.prompt}\n</system-reminder>\n")
    sys.stdout.flush()


def _emit_notice(message: str) -> None:
    sys.stdout.write(f"<agent-memory>\n<!-- memory hook notice: {message} -->\n</agent-memory>\n")


def _emit_context(context_text: str) -> None:
    sys.stdout.write("<agent-memory>\n")
    sys.stdout.write(context_text)
    if not context_text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.write("</agent-memory>\n")


def _dedupe_cache_path() -> Path:
    raw = os.environ.get("AGENT_MEMORY_HOOK_DEDUPE_PATH")
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "agent_memory_lite_hook_dedupe.json"


def _dedupe_key(event: dict[str, object], *, workspace: str, prompt: str) -> str:
    session = str(event.get("session_id") or event.get("transcript_path") or "")
    cwd = str(event.get("cwd") or "")
    payload = "\n".join([workspace, session, cwd, prompt])
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _should_emit_context(
    event: dict[str, object],
    *,
    workspace: str,
    prompt: str,
    cache_path: Path | None = None,
    ttl_seconds: float = DEFAULT_DEDUPE_TTL_SECONDS,
) -> bool:
    if ttl_seconds <= 0:
        return True
    key = _dedupe_key(event, workspace=workspace, prompt=prompt)
    cache_path = cache_path or _dedupe_cache_path()
    current_time = time.time()
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}

    previous = cache.get(key)
    if isinstance(previous, int | float) and current_time - float(previous) < ttl_seconds:
        return False

    cache[key] = current_time
    cutoff = current_time - max(ttl_seconds * 4, 10.0)
    compacted = {
        str(item_key): float(item_value)
        for item_key, item_value in cache.items()
        if isinstance(item_value, int | float) and float(item_value) >= cutoff
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(compacted, sort_keys=True), encoding="utf-8")
    except OSError:
        return True
    return True


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db-path", default=os.environ.get("AGENT_MEMORY_DB_PATH"))
    parser.add_argument("--vector-path", default=os.environ.get("AGENT_MEMORY_VECTOR_PATH"))
    parser.add_argument(
        "--workspace", default=os.environ.get("AGENT_MEMORY_WORKSPACE", DEFAULT_WORKSPACE)
    )
    return parser.parse_known_args()[0]


def main() -> int:  # noqa: PLR0915 - linear hook flow with explicit early returns per failure mode
    args = _parse_args()
    event = _read_event()
    prompt = str(event.get("prompt", "")).strip()
    if not prompt:
        return 0

    db_path = args.db_path
    vector_path = args.vector_path
    workspace = str(event.get("workspace_id") or args.workspace or "")

    # cwd auto-detect: when no per-project flags are present, walk the
    # registry to find which project root we are currently in. This is what
    # makes a single global hook route correctly across many projects.
    # The event may not carry a `cwd` field (some hosts strip it), so we
    # fall back to the hook process's own cwd, which is normally the chat
    # session's working directory.
    needs_resolve = not db_path or workspace in {"", "default"}
    if needs_resolve:
        cwd_candidates: list[Path] = []
        event_cwd = event.get("cwd")
        if event_cwd:
            cwd_candidates.append(Path(str(event_cwd)))
        with contextlib.suppress(OSError):
            cwd_candidates.append(Path(os.getcwd()))
        for candidate in cwd_candidates:
            resolved = _resolve_from_registry(candidate)
            if resolved:
                if not db_path and resolved["db_path"]:
                    db_path = resolved["db_path"]
                if not vector_path and resolved["vector_path"]:
                    vector_path = resolved["vector_path"]
                if workspace in {"", "default"} and resolved["workspace_id"]:
                    workspace = resolved["workspace_id"]
                break

    if not workspace:
        workspace = DEFAULT_WORKSPACE

    # If we still ended up on `default` and no DB path was supplied, the
    # working directory is not in the hub registry. Auto-bootstrap a
    # shared "global" fallback workspace under ~/.agent_memory/global/
    # and route the hook there, so a chat opened in any directory still
    # gets memory context. Set AGENT_MEMORY_HOOK_FALLBACK=disabled to
    # opt out and get the legacy "no workspace registered" notice
    # behavior instead (useful for strict project-mode setups).
    used_global_fallback = False
    fallback_cwd = ""
    if workspace == "default" and not db_path:
        if HOOK_FALLBACK_DISABLED:
            cwd_now = os.getcwd()
            _emit_notice(
                f"agent-memory-lite has no workspace registered for "
                f"cwd={cwd_now!r}. Hook fallback is disabled "
                "(AGENT_MEMORY_HOOK_FALLBACK=disabled). From the "
                "agent-memory-lite repo run `python scripts/setup_agent.py "
                "--project <path>` to register this project."
            )
            return 0
        fallback = _ensure_global_fallback()
        if fallback is None:
            cwd_now = os.getcwd()
            _emit_notice(
                f"agent-memory-lite has no workspace registered for "
                f"cwd={cwd_now!r} and the global fallback bootstrap "
                "failed. From the agent-memory-lite repo run "
                "`python scripts/setup_agent.py --project <path>` to "
                "register this project explicitly."
            )
            return 0
        workspace = fallback["workspace_id"]
        db_path = fallback["db_path"]
        vector_path = fallback["vector_path"]
        # Hook silently fell back to an empty global workspace because no
        # registered project root was found above ``cwd``. The agent must
        # see this fact -- otherwise it spends the whole session looking
        # at ``<core_memory/>`` self-closing skeleton tags and assumes
        # there's nothing to remember (real cause: wrong cwd at session
        # start). We attach a visible <hook_notice> to the final envelope
        # below so every prompt surfaces the breadcrumb.
        used_global_fallback = True
        fallback_cwd = os.getcwd()

    if os.environ.get("AGENT_MEMORY_HOOK_DEBUG", "").strip().lower() in {"1", "true", "yes"}:
        debug_path = Path(tempfile.gettempdir()) / "agent_memory_lite_hook_debug.log"
        with contextlib.suppress(OSError):
            debug_path.write_text(
                json.dumps(
                    {
                        "ts": time.time(),
                        "cwd": os.getcwd(),
                        "event_keys": sorted(event.keys()),
                        "event_cwd": event.get("cwd"),
                        "args_db": args.db_path,
                        "args_workspace": args.workspace,
                        "resolved_workspace": workspace,
                        "resolved_db": db_path,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    if not _should_emit_context(event, workspace=workspace, prompt=prompt):
        return 0

    # v1.10 correction capture (best-effort, never raises). Runs AFTER
    # the dedupe gate so a replayed identical prompt within the dedupe
    # TTL doesn't double-ingest the same (claim, correction) pair —
    # without this ordering, the same correction could be captured
    # twice and produce two near-duplicate memory_candidates. The
    # capture writes two episodes (claim + correction); the
    # CorrectionExtractor on the server side runs during
    # ingest_episode and proposes a memory_candidate(kind=CORRECTION)
    # for review.
    if CORRECTION_DETECT_ENABLED and CORRECTION_TRANSCRIPT_READ_ENABLED:
        try:
            _maybe_capture_correction(
                event=event,
                workspace=workspace,
                db_path=db_path,
                vector_path=vector_path,
                user_prompt=prompt,
            )
        except Exception:
            # capture is opportunistic; never break the hook on failure
            pass

    # Memory-discipline audit (best-effort, never raises). When the
    # previous assistant turn did real file work without any memory
    # write, this emits a <system-reminder> with forced language that
    # the agent must acknowledge before continuing. Promotes the
    # Memory-first / write-resolve discipline from background envelope
    # to foreground system context, which Claude follows more reliably.
    try:
        _maybe_emit_memory_audit(event=event)
    except Exception:
        pass

    payload: dict[str, object] = {
        "workspace_id": workspace,
        "query": prompt[:1000],
        "max_tokens": DEFAULT_MAX_TOKENS,
    }
    task_id = event.get("task_id") or event.get("session_id")
    if task_id:
        payload["task_id"] = str(task_id)

    headers: dict[str, str] = {}
    if db_path:
        headers["X-Memory-DB-Path"] = db_path
    if vector_path:
        headers["X-Memory-Vector-Path"] = vector_path

    try:
        response = httpx.post(
            f"{DEFAULT_BASE}/memory/get_context",
            json=payload,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
    except httpx.ConnectError:
        # HTTP service down — fall back to direct SQLite + FTS so the
        # agent still sees core_memory / behavior_instructions / active
        # decisions / FTS-matched chunks. Vector ranking and graph walk
        # are skipped (would require loading the embedding model, ~3s
        # cold start unacceptable per-prompt). Import locally so the
        # hook works both as a script (scripts/ on sys.path) and as a
        # package import (`from scripts.inject_memory_context ...`).
        if db_path:
            try:
                # under both `python scripts/inject_memory_context.py` and
                # `from scripts.inject_memory_context import ...` execution.
                from inject_memory_fts_fallback import build_fts_only_context  # noqa: PLC0415
            except ImportError:
                try:
                    from scripts.inject_memory_fts_fallback import (  # noqa: PLC0415
                        build_fts_only_context,
                    )
                except ImportError:
                    build_fts_only_context = None  # type: ignore[assignment]
            if build_fts_only_context is not None:
                fallback_text = build_fts_only_context(
                    db_path=db_path, workspace_id=workspace, query=prompt[:1000]
                )
                if fallback_text:
                    _emit_context(fallback_text)
                    return 0
        _emit_notice(
            f"agent-memory-lite is not running on {DEFAULT_BASE} and the "
            "FTS fallback found nothing. Start the service with "
            "`python scripts/serve.py` from the agent-memory-lite repo "
            "(hub mode auto-enables when a project registry exists)."
        )
        return 0
    except httpx.TimeoutException:
        _emit_notice(
            f"agent-memory-lite at {DEFAULT_BASE} did not answer within "
            f"{DEFAULT_TIMEOUT:.0f}s. The service is probably warming embedders "
            "or applying migrations; the next prompt should succeed."
        )
        return 0
    except httpx.HTTPError as exc:
        _emit_notice(
            f"agent-memory-lite request to {DEFAULT_BASE} failed: {exc!s}. "
            "Check `curl http://127.0.0.1:8765/health` and the service "
            "console; the next prompt will retry."
        )
        return 0

    if response.status_code == 400:
        body_text = response.text[:300].replace("\n", " ")
        _emit_notice(
            f"agent-memory-lite rejected workspace_id={workspace!r} (400). "
            "The service is in strict mode but this hook routes via "
            "X-Memory-DB-Path. Restart the service with hub mode enabled: "
            "from agent-memory-lite repo run `python scripts/serve.py --hub` "
            f"or set MEMORY_HUB_MODE=true. Server detail: {body_text}"
        )
        return 0
    if response.status_code >= 400:
        body_text = response.text[:200].replace("\n", " ")
        _emit_notice(f"agent-memory-lite returned HTTP {response.status_code}: {body_text}")
        return 0

    try:
        body = response.json()
    except json.JSONDecodeError:
        _emit_notice("agent-memory-lite returned non-JSON")
        return 0

    text = body.get("context_text")
    if not isinstance(text, str) or not text.strip():
        _emit_notice("agent-memory-lite returned no context")
        return 0

    if used_global_fallback:
        # Prepend a visible <hook_notice> block so the agent sees the
        # real reason the envelope is mostly empty: wrong cwd at session
        # start dropped the hook into the empty global workspace. The
        # comment lists the registry workspaces it COULD have routed to,
        # so the operator (or agent) can fix it next session.
        try:
            registry_ws = sorted(str(e.get("id", "?")) for e in _list_registry_entries()) or [
                "<empty>"
            ]
        except Exception:
            registry_ws = ["<unavailable>"]
        notice = (
            f'<hook_notice severity="warn" code="global_fallback">\n'
            f"  Hook resolved no registered workspace for cwd={fallback_cwd!r}; "
            f"falling back to empty `global` workspace. Real project memory is "
            f"NOT visible in this envelope. Registered workspaces: {registry_ws}. "
            f"To fix: open Claude Code from a project root listed above, OR "
            f"set AGENT_MEMORY_WORKSPACE=<id> in the env, OR pass "
            f"--workspace=<id> in the hook command in ~/.claude/settings.json.\n"
            f"</hook_notice>\n"
        )
        text = notice + text

    _emit_context(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
