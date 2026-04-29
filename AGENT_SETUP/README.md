# AGENT_SETUP — paste-and-forget prompts

Two self-contained prompts. Pick one, paste it as the first user message in
a new chat (or as a follow-up in an active chat), and the agent does the
rest autonomously.

| File | Use when |
|---|---|
| [`01_FRESH_PROJECT.md`](01_FRESH_PROJECT.md) | New chat. The agent is in a project but has never used memory here. It will detect, bootstrap, and verify everything. |
| [`02_CAPTURE_THIS_CHAT.md`](02_CAPTURE_THIS_CHAT.md) | Active chat. You've already done work in this conversation and want it persisted to memory before the chat closes. |

Both prompts assume the `agent-memory-lite` repo is checked out somewhere
on this machine and `pip install -e ".[mcp]"` has been done in its venv.
The prompts walk the agent through detection: it tries the
`AGENT_MEMORY_LITE_HOME` env var first, then asks Python where the
installed package lives (`python -c "import agent_memory_lite, pathlib;
print(pathlib.Path(agent_memory_lite.__file__).parents[2])"`), then
common workspace locations, and finally falls back to asking you.

After either prompt finishes, the agent should:
1. Tell you what it set up (paths, files written).
2. Confirm the memory tools are working (a quick `memory_get_context` round-trip).
3. Use the project's established workspace id, or the project directory name
   when none is established.
4. Run read-only `scripts/memory_audit.py --workspace <workspace_id> --json`
   and `scripts/memory_hygiene.py --workspace <workspace_id> --json` checks
   when the repo is available.
5. Run `scripts/memory_mcp_smoke.py --workspace <workspace_id> --json` after
   MCP setup or restart so the actual agent-facing handler is proven fast.
6. Run `scripts/memory_candidate_triage.py --workspace <workspace_id> --json`
   when candidate extraction is enabled, so stale/high-value candidates are
   reviewed instead of becoming invisible backlog.
7. If hygiene only reports missing capability links, run
   `scripts/memory_auto_triage.py --workspace <workspace_id> --json`; apply
   with `--apply --backup-first` only after reviewing the dry-run output.
8. For a one-command trust report, run
   `scripts/memory_trust_dashboard.py --workspace <workspace_id> --project-root . --json`.
9. Continue with whatever you wanted to do.

For research-heavy projects, the capture prompt also tells the agent to preserve
research hypotheses, snapshots, experiments, results, and insights with the
first-class research tools instead of burying them inside raw episodes. For
operations-heavy projects, it tells the agent to preserve reusable roles,
skills, and playbooks with the capability tools.
Extraction candidates are review-first: promote supported candidates and reject
weak ones instead of turning every extracted sentence into an active decision.

If the agent reports that MCP tools aren't visible in its tool list, you
need to **restart whichever runtime is hosting it**:

| Runtime | What to restart | Where the MCP config lives |
|---|---|---|
| Claude Code | Close and reopen the app | `<project>/.claude/settings.json` (project mode) or `~/.claude/settings.json` (global) |
| Codex | Close and reopen the editor / CLI | `~/.codex/config.toml` — Codex reads MCP globally only. `--project` mode still writes `<project>/AGENTS.md` and the DB, but the MCP entry must come from the global setup. Run `python scripts/setup_agent.py` (no `--project`) once for that, then restart. |
| Cursor | Close and reopen Cursor | `~/.cursor/mcp.json` (or per-project equivalent if your build supports it) |
| Other MCP-aware client | Whatever the client treats as a session boundary | check the client's docs |

This is not a bug in agent-memory-lite — every MCP client reads its
config at startup, not on every prompt.
