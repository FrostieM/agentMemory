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

If the agent reports that MCP tools aren't visible in its tool list, you'll
need to **restart your agent runtime** (Claude Code: close and reopen).
That's a Claude Code limitation — settings.json is read at startup, not on
every prompt.
