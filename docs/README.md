# Documentation map

agent-memory-lite ships a compact active documentation set. Read top-to-bottom
if you're new; jump straight to the topic file otherwise.

## Start here

| File | Role | Length |
|---|---|---|
| [`../README.md`](../README.md) | Project overview, install, first-run walkthrough | long |
| [`AGENT_CHEATSHEET.md`](AGENT_CHEATSHEET.md) | One-page when-to-call-what for agents | 1 page |
| [`AGENT_CONTRACT.md`](AGENT_CONTRACT.md) | Full operating contract: actionable rules | ~317 lines |

## Reference

| File | Use when |
|---|---|
| [`MEMORY_API.md`](MEMORY_API.md) | You need the JSON shape of an HTTP endpoint or MCP tool. |
| [`CODE_MEMORY_GUIDE.md`](CODE_MEMORY_GUIDE.md) | You're using the compact v3 code-memory surface. |
| [`OPERATIONS.md`](OPERATIONS.md) | Day-to-day operator workflow: service start/stop, hooks, troubleshooting. |
| [`../CHANGELOG.md`](../CHANGELOG.md) | What changed in each release, most recent at top. |

## Architecture decisions

| File | What it locks in |
|---|---|
| [`adr/0004-hub-mode.md`](adr/0004-hub-mode.md) | Hub-mode service routing rationale. |
| [`adr/0005-asymmetric-isolation.md`](adr/0005-asymmetric-isolation.md) | Cross-workspace read OK, write blocked. |

## Historical material

Legacy v1/v2 deep dives are no longer active documentation. See
[`REMOVED.md`](REMOVED.md) for the removal map; detailed historical notes live
in git history rather than the active docs tree.

## Contract sync

The agent operating contract lives only in `AGENT_CONTRACT.md`. The repo's
`CLAUDE.md` and `AGENTS.md` carry an exact copy of the contract between
`<!-- agent-memory-lite-contract:begin/end -->` markers, re-injected by:

```bash
python scripts/setup_agent.py --sync-repo
```

CI runs the same sync and fails on any drift, so direct edits to the marker
block in `CLAUDE.md` / `AGENTS.md` get rejected. To push fresh contract into a
project's local `CLAUDE.md`:

```bash
python scripts/setup_agent.py --project /path/to/project
```
