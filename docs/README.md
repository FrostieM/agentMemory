# Documentation map

agent-memory-lite ships a layered documentation set. Read **top-to-bottom** if
you're new; jump straight to the topic file otherwise.

## Start here

| File | Role | Length |
|---|---|---|
| [`../README.md`](../README.md) | Project overview, install, first-run walkthrough | long |
| [`AGENT_CHEATSHEET.md`](AGENT_CHEATSHEET.md) | One-page when-to-call-what for agents | 1 page |
| [`AGENT_CONTRACT.md`](AGENT_CONTRACT.md) | Full operating contract — actionable rules | ~317 lines |

## Reference (look-up tables)

| File | Use when |
|---|---|
| [`MEMORY_API.md`](MEMORY_API.md) | You need the JSON shape of an HTTP endpoint or MCP tool. |
| [`CODE_MEMORY_GUIDE.md`](CODE_MEMORY_GUIDE.md) | You're using the v1.4 → v2.1.x code-memory tools (`memory_find_symbols`, etc.). |
| [`OPERATIONS.md`](OPERATIONS.md) | Day-to-day operator workflow: service start/stop, hooks, troubleshooting. |
| [`../CHANGELOG.md`](../CHANGELOG.md) | What changed in each release — most recent at top. |

## Architecture decisions

| File | What it locks in |
|---|---|
| [`adr/0004-hub-mode.md`](adr/0004-hub-mode.md) | Hub-mode service routing rationale. |
| [`adr/0005-asymmetric-isolation.md`](adr/0005-asymmetric-isolation.md) | Cross-workspace read OK, write blocked. |

## Per-release deep dives (kept for archaeology)

These files capture the design + calibration evidence for individual releases.
The day-to-day operator usually does NOT need them — `CHANGELOG.md` summarises
the same info. Kept because source comments and calibration scripts still link
to them.

| File | Release | Topic |
|---|---|---|
| [`V1_1_0.md`](V1_1_0.md) | 1.1.0 | Default-ON quality features, env-flag map |
| [`V1_1_0_CALIBRATION.md`](V1_1_0_CALIBRATION.md) | 1.1.0 | Calibration evidence on a representative project replay |
| [`V1_2_0.md`](V1_2_0.md) | 1.2.0 | v1.10 correction-aware loop runbook |
| [`V1_4_TO_V2_ROADMAP.md`](V1_4_TO_V2_ROADMAP.md) | 1.4–2.1 | Code-memory roadmap |
| [`V2_CALIBRATION.md`](V2_CALIBRATION.md) | 2.0 | v2 retrieval calibration |
| [`POST_V2_ROADMAP.md`](POST_V2_ROADMAP.md) | post-2.x | Speculative future work |

## Contract sync

The agent operating contract lives **only** in `AGENT_CONTRACT.md`. The repo's
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
