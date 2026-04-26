# AGENT_SETUP — paste-and-forget prompts

Two self-contained prompts. Pick one, paste it as the first user message in
a new chat (or as a follow-up in an active chat), and the agent does the
rest autonomously.

| File | Use when |
|---|---|
| [`01_FRESH_PROJECT.md`](01_FRESH_PROJECT.md) | New chat. The agent is in a project but has never used memory here. It will detect, bootstrap, and verify everything. |
| [`02_CAPTURE_THIS_CHAT.md`](02_CAPTURE_THIS_CHAT.md) | Active chat. You've already done work in this conversation and want it persisted to memory before the chat closes. |

Both prompts assume the `agent-memory-lite` repo is checked out somewhere on
this machine. They tell the agent to find it (common locations + git
search). Default: `C:/Users/Osino/Desktop/work/agent-memory-lite`.

After either prompt finishes, the agent should:
1. Tell you what it set up (paths, files written).
2. Confirm the memory tools are working (a quick `memory_get_context` round-trip).
3. Continue with whatever you wanted to do.

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
