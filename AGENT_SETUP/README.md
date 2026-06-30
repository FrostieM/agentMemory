# AGENT_SETUP -- project-initialization prompts

Two self-contained prompts. Pick the one that matches your project, paste it as
the first user message in a new chat inside that project, and the agent does the
rest. Both wire memory if needed, index the code, interview you once, and seed
your answers into memory as the project's starting decisions / behavior rules /
build-test-run playbook.

| File | Use when |
|---|---|
| [`01_NEW_PROJECT.md`](01_NEW_PROJECT.md) | A fresh / greenfield project whose memory is empty. The agent wires memory, indexes whatever code exists, interviews you (~7 questions), and seeds your answers. |
| [`02_EXISTING_PROJECT.md`](02_EXISTING_PROJECT.md) | A project already worked on -- existing code, git history, docs, prior AI sessions. Does everything `01` does AND first mines the existing codebase / manifests / docs / git history into memory on its own, marked agent-inferred; your interview answers then override anything it guessed. |

Both assume the `agent-memory-lite` repo is checked out on this machine and
`pip install -e ".[mcp]"` (or `.[dev,mcp]`) has been done in its venv. Each
prompt locates the repo itself: it tries the `AGENT_MEMORY_LITE_HOME` env var,
then `python -c "import agent_memory_lite, pathlib;
print(pathlib.Path(agent_memory_lite.__file__).resolve().parents[2])"`, then
common workspace locations, and finally asks you. Each also runs
`setup_agent.py --project` if the project is not wired yet, so you do not need a
separate setup step.

## What's safe by default after these prompts run

`setup_agent.py --project` (which both prompts call) bakes the asymmetric
isolation contract into the project's MCP env:

- Reads from this project's chat to ANY registered workspace are allowed -- you
  can ask to inspect another registered workspace's memory and the agent routes
  the read to that DB via the workspace registry.
- Writes from this project's chat to ANY workspace other than its own are
  blocked at the strict-isolation guard. The agent refuses and asks you to
  switch contexts.

For full cross-workspace access (read AND write -- useful for batch
maintenance), open a chat in the **parent directory** (no project pin) or run
the HTTP service with `MEMORY_HUB_MODE=true`. `serve.py` accepts `--hub` /
`--strict`; default is hub mode whenever the registry has at least one entry.

## If the memory tools are not in the agent's tool list

Every MCP client reads its config at startup, not on every prompt, so a
just-wired project needs a one-time **restart of whichever runtime hosts the
agent**:

| Runtime | What to restart | Where the MCP config lives |
|---|---|---|
| Claude Code | Close and reopen the app | `<project>/.claude/settings.json` (project mode) or `~/.claude/settings.json` (global) |
| Codex | Close and reopen the editor / CLI | `~/.codex/config.toml` -- Codex reads MCP globally only. `--project` mode still writes `<project>/AGENTS.md` and the DB, but the MCP entry comes from the global setup: run `python scripts/setup_agent.py` (no `--project`) once, then restart. |
| Cursor | Close and reopen Cursor | `~/.cursor/mcp.json` (or the per-project equivalent your build supports) |
| Other MCP-aware client | Whatever the client treats as a session boundary | check the client's docs |

After the restart, paste the prompt again -- both prompts are idempotent
(`memory_search` dedups), so re-running is safe.
