# agent-memory-lite

Local memory subsystem for an AI agent. Runs from a virtualenv. No Docker, no Postgres,
no cloud LLM/vector providers.

```
Agent ──HTTP / MCP / in-process──▶ Memory Service (FastAPI on 127.0.0.1:8765)
                                    │
                                    ├─ SQLite (WAL, FTS5)   ── episodes, chunks, decisions,
                                    │                          task_state, core_memory,
                                    │                          theories, procedural_rules, entities,
                                    │                          facts, audit_log
                                    ├─ LanceDB              ── embedded vector store
                                    ├─ sentence-transformers── intfloat/multilingual-e5-small (default)
                                    └─ Ollama (mandatory)   ── qwen2.5:7b-instruct for extraction
```

## Status

v1 feature-complete: all six phases of the spec are landed (episodes + FTS,
hybrid retrieval, decisions/task/core/procedural, lite temporal graph, file
ingestion, compaction + evals + MCP tool registry). The research-lab extension
adds theories, snapshots, experiments, results, concepts, insights, query-ranked
context sections, and a status CLI. The capability extension adds first-class
agent roles, reusable skills, and repeatable playbooks so execution knowledge is
retrievable instead of buried in episodes. The behavior-instruction extension
adds first-class communication style, operating rules, project conventions,
priority, scope, and conflict-policy memory. See `SESSION_STATE.md` for current
verification counts.

The integrity extension adds reviewable memory candidates, maintenance events,
a workspace manifest, and a retrieval-integrity audit so SQLite, FTS, vector,
workspace, and research-discipline drift cannot stay silent.

## Requirements

- **Python 3.13 recommended** (3.12 supported, 3.14 supported but `torch` wheels may lag).
- **Ollama** required for production LLM-driven extraction. The service refuses to start
  unless Ollama responds at `LLM_BASE_URL`, **or** `OLLAMA_PROBE_SKIP=true` (use the
  skip flag for tests / first-run smoke checks only).
- Windows / macOS / Linux. Paths are normalized; tested on Windows.

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate

pip install -e ".[dev]"
```

If `torch` wheels are unavailable for your Python version, install Python 3.13 alongside
(via `pyenv-win`, `rye`, or the official installer) and recreate the venv.

## Set up Ollama (recommended for full feature set)

```bash
# https://ollama.com/download
ollama pull qwen2.5:7b-instruct
```

The default `LLM_BASE_URL` is `http://127.0.0.1:11434`. Without Ollama, set
`OLLAMA_PROBE_SKIP=true` in `.env` and the heuristic extractor still runs;
LLM-driven candidate extraction is simply disabled until Ollama is reachable.

## Quick verification (≈ 2 minutes, no Ollama needed)

This is the end-to-end check the project ships with. It boots the service,
seeds the workspace with a representative session, exercises every public
endpoint, and runs the eval harness.

## Theory memory

Episodes are the audit log: what happened, when, and with what evidence. They
should not be the only place where learning lives. Use **theories** for working
claims that need evidence and experiments:

- `POST /memory/write_theory` records a hypothesis with `claim`, `mechanism`,
  `predictions`, `validation_criteria`, `experiment_plan`,
  `dependent_decision_ids`, `tags`, `status`, `confidence`, and `importance`.
- `POST /memory/add_theory_evidence` attaches supporting, refuting, mixed,
  neutral, or experiment evidence to a theory.
- `POST /memory/list_theories` retrieves the relevant theory set, optionally
  including recent evidence. Responses include `evidence_count` and signed
  `evidence_strength`, so rejected/weak theories remain visible as negative
  knowledge instead of disappearing into episodes.
- `POST /memory/get_context` includes an `<active_theories>` section ahead of
  retrieved chunks, so agents see the current research agenda instead of
  rediscovering it from hundreds of episodes.

## Research-lab memory

Theories say what might be true. The research-lab layer tracks the work needed
to test those theories:

- `POST /memory/register_snapshot` catalogs a data snapshot with SQLite/DuckDB
  paths, table counts, row totals, source, build metadata, and time windows.
- `POST /memory/write_experiment` creates a planned/running test linked to a
  `theory_id` and/or `snapshot_id`, including cohort definition, command, and
  success criteria.
- `POST /memory/add_experiment_result` records the result, marks the experiment
  completed, attaches evidence to the linked theory, updates theory
  confidence/status, and creates a contradiction insight for high-confidence
  refuting or mixed evidence.
- `POST /memory/upsert_concept` stores shared vocabulary for metrics, gates,
  cohorts, and artifacts.
- `POST /memory/distill_insight` promotes lessons from raw episodes into an
  actionable backlog.
- `POST /memory/list_research_agenda` returns the current snapshots, open
  experiments, insights, and concepts for a query.
- `POST /memory/get_context` includes a `<research_agenda>` section after
  `<active_theories>`, so agents see the lab backlog before raw retrieved
  chunks.
- `scripts/research_status.py` prints a one-command report for active theories,
  snapshots, open experiments, insights, concepts, and whether
  `memory_get_context` is surfacing the research sections.

`memory_get_context` query-ranks and caps active decisions before rendering
theories and agenda, so old architectural choices do not bury current research
work as the memory grows.

## Agent capability memory

Episodes record what happened. Decisions record what was chosen. Research
objects record what should be studied. Capability memory records how agents
should execute work:

- `POST /memory/upsert_agent_role` stores a role with purpose,
  responsibilities, boundaries, handoff triggers, tools, confidence, and source.
- `POST /memory/upsert_agent_skill` stores a reusable skill with when-to-use
  cues, inputs, outputs, tools, and related roles.
- `POST /memory/upsert_agent_playbook` stores a repeatable workflow with
  triggers, ordered steps, success criteria, and required skills.
- `POST /memory/list_agent_capabilities` retrieves the relevant roles, skills,
  and playbooks for a query.
- `POST /memory/link_capability` links a role, skill, or playbook to a theory,
  evidence item, experiment, insight, candidate, or decision with an explicit
  relation such as `method`, `reviewer`, `critique_lens`, or
  `validation_playbook`.
- `POST /memory/list_capability_links` shows which capabilities influence a
  target object.
- `POST /memory/get_context` includes an `<agent_capabilities>` section after
  `<research_agenda>`, and linked capabilities are rendered inside theories,
  experiments, and insights so roles and skills can directly shape hypothesis
  review and research execution.

Recommended flow:

```text
define role -> define reusable skill -> define playbook
            -> link capability to theory/experiment
            -> verify via get_context
```

Example playbook:

```json
{
  "workspace_id": "<workspace_id>",
  "name": "Non-destructive live audit",
  "goal": "Confirm live flow without changing data.",
  "triggers": ["The user asks whether the live system works"],
  "steps": ["Read memory context", "Check health endpoints", "Report blockers"],
  "success_criteria": ["No reset was performed", "The report cites exact evidence"],
  "required_skills": ["Live flow audit"],
  "confidence": 0.85
}
```

Example capability link:

```json
{
  "workspace_id": "<workspace_id>",
  "target_type": "theory",
  "target_id": "th_...",
  "capability_type": "skill",
  "capability_name": "Replay and backtest design",
  "relation": "method",
  "rationale": "This hypothesis must be tested with replay before policy changes.",
  "strength": 0.9
}
```

## Behavior instruction memory

Procedural rules are still supported for simple durable rules. Use behavior
instructions when the agent should consistently change how it communicates or
operates:

- `POST /memory/upsert_behavior_instruction` stores a named instruction with
  `kind`, `scope`, `priority`, `rule`, `rationale`, `applies_to`,
  `conflict_policy`, confidence, source/review metadata, optional expiry,
  conflict group, and active state.
- `POST /memory/list_behavior_instructions` retrieves relevant behavior
  instructions for review.
- `POST /memory/get_context` includes a high-priority
  `<behavior_instructions>` section directly after `<core_memory>`, before task
  state, decisions, theories, and retrieved chunks.

Supported kinds: `communication_style`, `operating_rule`,
`project_convention`, `workflow_preference`, and `role_guidance`.

Supported conflict policies: `current_user_wins`, `system_wins`,
`higher_priority_wins`, `most_specific_wins`, and `latest_wins`. Store user
preferences with `current_user_wins` unless the rule is a non-negotiable safety
or project constraint. Current user instructions and higher-level system
instructions still outrank stored memory.

Example behavior instruction:

```json
{
  "workspace_id": "<workspace_id>",
  "name": "Evidence-first operational reports",
  "kind": "communication_style",
  "scope": "workspace",
  "priority": "user_preference",
  "rule": "When reporting incidents, lead with exact issue, evidence, fix, and remaining risk.",
  "rationale": "The user needs concrete operational evidence rather than generic status language.",
  "applies_to": ["incident reports", "runtime audits"],
  "conflict_policy": "current_user_wins",
  "source_type": "user_direct",
  "source_id": "chat-20260430",
  "reviewed_by": "operator",
  "reviewed_at": "2026-04-30T00:00:00+00:00",
  "expires_at": null,
  "conflict_group": "incident-report-style",
  "confidence": 0.95
}
```

Expired behavior instructions are suppressed from `memory_get_context`. Use
`/memory/explain_context` to inspect suppressed behavior instructions and
reasons such as `expired`, `inactive`, or `query_mismatch`. Instructions copied
from untrusted external content should stay as review candidates until a human
or trusted agent review assigns safe provenance.

Recommended flow:

```text
register_snapshot -> write_theory -> write_experiment -> add_experiment_result
                                      -> confidence/status update
                                      -> contradiction insight if needed
```

Example experiment result:

```json
{
  "workspace_id": "<workspace_id>",
  "experiment_id": "exp_...",
  "kind": "supporting",
  "summary": "Favorite-side source flips stayed positive after fee assumptions.",
  "metrics": {"n": 144, "net_edge_bps": 31.2},
  "artifact_path": "reports/analitic/source_flip_replay.md",
  "confidence": 0.8
}
```

Example theory:

```json
{
  "workspace_id": "<workspace_id>",
  "title": "Source-flip tennis favorites",
  "domain": "trading.paper.edge",
  "claim": "Source-flip trades on tennis favorites may carry short-lived edge.",
  "mechanism": "The source wallet may react before public odds fully adjust.",
  "predictions": ["favorite-side flips outperform underdog-side flips"],
  "validation_criteria": [
    "minimum 100 settled trades",
    "net edge remains positive after fee assumptions"
  ],
  "experiment_plan": "Replay source-flip fills by sport and side on the latest VPS snapshot.",
  "dependent_decision_ids": ["dec_..."],
  "tags": ["trading-bot", "source-flip", "tennis", "favorite"],
  "status": "testing"
}
```

Use `status="rejected"` for disproven theories. A rejected theory is an
anti-theory: it records that a tempting explanation or edge did **not** survive
measurement. Keep it queryable with refuting evidence and metrics rather than
burying it in an episode.

```bash
# 1. configure (one-off)
cp .env.example .env
# Either install Ollama (see above) or skip the probe:
sed -i 's/^OLLAMA_PROBE_SKIP=false/OLLAMA_PROBE_SKIP=true/' .env

# 2. unit + property + integration + e2e tests (~3s)
pytest -q

# 3. start a fresh DB and the service
rm -rf .agent_memory
python scripts/bootstrap_db.py
python -m agent_memory_lite          # binds 127.0.0.1:8765 in this terminal

# 4. in a second terminal, smoke-test every route end-to-end:
python scripts/seed_demo_session.py
```

`scripts/seed_demo_session.py` ingests 10 episodes (one with secrets that get
redacted in flight), writes 3 architectural decisions, upserts a task state,
queries `POST /memory/get_context` (the agent-facing surface), runs an exact
FTS lookup via `POST /memory/search`, and finally runs the eval harness via
`POST /memory/run_evals`. The expected outcome:

```
=== POST /memory/run_evals ===
{
  "cases_run": 13,
  "cases_passed": 13,
  "retrieval_recall_at_10": 1.0,
  "retrieval_precision_at_10": 1.0,
  "stale_fact_rate": 0.0,
  "secret_leak_count": 0,
  "prompt_injection_failures": 0,
  "failures": []
}
```

Health check at any time:

```bash
curl http://127.0.0.1:8765/health
```

`/health` includes `retrieval_integrity`. A degraded FTS/vector/workspace
manifest/workspace pollution check, open maintenance event, or dangling
capability link changes health status to `degraded`; repair is never automatic.
Candidate/research hygiene problems appear as `warnings` so they are visible
without pretending the retrieval substrate is physically broken.

Read-only audit:

```bash
python scripts/memory_audit.py --workspace <workspace_id> --json
```

Detailed memory hygiene report:

```bash
python scripts/memory_hygiene.py --workspace <workspace_id> --json
curl "http://127.0.0.1:8765/memory/hygiene_report?workspace_id=<workspace_id>"
```

`missing_capability_link` findings include `suggested_capability_links` payloads
with the target id, capability id/name, relation, rationale, and strength. Use
those payloads as review candidates for `memory_link_capability`; hygiene does
not create links automatically.

Strict content-quality gate:

```bash
python scripts/memory_quality_gate.py --workspace <workspace_id> --json
curl "http://127.0.0.1:8765/memory/quality_gate?workspace_id=<workspace_id>"
```

The quality gate is stricter than hygiene. It marks important untestable
theories, terminal theories without evidence, important experiments without
success criteria, important decisions without provenance, expired active
behavior instructions, and active behavior instructions sourced from untrusted
content as `degraded`.
Use it before treating a memory DB as a research-grade source of truth.

Bounded auto-triage for those suggestions:

```bash
python scripts/memory_auto_triage.py --workspace <workspace_id> --json
python scripts/memory_auto_triage.py --workspace <workspace_id> --apply --backup-first --json
```

The default is dry-run. Mutating mode requires `--backup-first`, applies only
suggestions above the configured `--min-strength` and `--min-match-score`
thresholds, and leaves semantic gaps such as weak theories or stale experiments
for explicit review.

Read-only candidate review queue:

```bash
python scripts/memory_candidate_triage.py --workspace <workspace_id> --json
```

This groups unreviewed candidates by kind/status and flags stale or high-value
items that need explicit promote/reject review.

Live watchdog over integrity, retrieval sentinels, and hygiene:

```bash
python scripts/memory_watchdog.py --workspace-id <workspace_id> --db .agent_memory/memory.db --vectors .agent_memory/vectors.lance --json
python scripts/memory_watchdog.py --workspace-id <workspace_id> --sentinels .agent_memory/retrieval_sentinels.yaml --json
```

The watchdog writes JSON artifacts under `.agent_memory/audit_runs/`, updates
the workspace manifest audit timestamp, and opens a maintenance event only when
integrity, retrieval quality, or hygiene is degraded/warning. It never repairs.

Compare two trust reports:

```bash
python scripts/memory_diff.py --before .agent_memory/audit_runs/old.json --after .agent_memory/audit_runs/new.json --json
```

`memory_diff.py` accepts audit, watchdog, or trust-dashboard JSON. It reports
status regressions, count deltas, component status changes, new failures, and
resolved warnings so drift between two checks is explicit.

Benchmark memory operations:

```bash
python scripts/memory_benchmark.py --workspace <workspace_id> --db-path .agent_memory/memory.db --query "workspace manifest" --runs 3 --json
```

The benchmark measures `PRAGMA quick_check`, integrity audit, hygiene report,
quality gate, FTS search, and `memory_get_context`. It is FTS-only by default
for fast CI/deploy checks; pass `--with-vector` when you intentionally want to
measure embedding/vector latency.

MCP and contract smoke checks:

```bash
python scripts/memory_mcp_smoke.py --workspace <workspace_id> --db-path .agent_memory/memory.db --vector-path .agent_memory/vectors.lance --require-behavior --require-capabilities --json
python scripts/memory_contract_check.py --root . --workspace <workspace_id> --json
python scripts/memory_backup_restore_check.py --workspace <workspace_id> --db-path .agent_memory/memory.db --vector-path .agent_memory/vectors.lance --json
python scripts/memory_trust_dashboard.py --workspace <workspace_id> --db-path .agent_memory/memory.db --vector-path .agent_memory/vectors.lance --project-root . --json
```

`memory_mcp_smoke.py` launches a fresh Python process and checks that the MCP
`memory_get_context` handler returns quickly with behavior/capability sections.
`memory_contract_check.py` catches stale generic instructions such as hard-coded
`default` workspace examples. `memory_backup_restore_check.py` copies the DB and
vector store to a temporary restore target and audits the copy. The trust
dashboard composes the audit, hygiene, watchdog, MCP, candidate, contract, and
restore checks into one report.

Agent workflow wrapper:

```bash
python scripts/memory_workflow.py --workspace <workspace_id> preflight --query "task summary" --task-id task-123 --json
python scripts/memory_workflow.py --workspace <workspace_id> complete --task-id task-123 --goal "Fix issue" --raw-text "Implemented and verified ..." --json
```

Use `preflight` before non-trivial work to fetch the same context an agent
should inspect. Use `complete` after work to write an episode and task state in
one step. Add `--api-token-file .agent_memory/token` when optional HTTP token
auth is enabled, and `--dry-run` to inspect payloads without writing.

Sentinel files are YAML lists. They should contain project-specific ids kept
outside generic docs, for example:

```yaml
- name: known_recent_incident
  query: "exact token plus paraphrase"
  expected_ids: ["chk_..."]
  expected_context_ids: ["th_...", "dec_..."]
  expected_sources: ["fts", "vector"]
  expected_sections: ["active_theories", "retrieved_chunks"]
  top_k: 10
  max_tokens: 2500
```

Retrieval-quality reports include `recall_at_k`, `mrr`, `ndcg_at_k`, and
`context_hit_rate`. Use exact-token cases to protect FTS, paraphrase cases to
protect vector retrieval, and `expected_context_ids` to prove that
`memory_get_context` included the right theory, decision, or chunk in the final
agent envelope.

Explain a `memory_get_context` result:

```bash
curl -s -X POST http://127.0.0.1:8765/memory/explain_context \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"<workspace_id>","query":"why did this not show up?","max_tokens":2500}'
```

The explain endpoint is read-only. It reports FTS/vector source candidates,
merged scores, included ids, section counts, suppressed behavior instructions,
and the reason a scored chunk was or was not included in the final context.

Explicit repair, with backups first:

```bash
python scripts/memory_audit.py --workspace <workspace_id> --repair-fts --backup-first
python scripts/memory_audit.py --workspace <workspace_id> --repair-vectors --backup-first
python scripts/memory_audit.py --workspace <workspace_id> --repair-embedding-refs --backup-first
```

Vector repair also stamps `vector_index_metadata` with provider name,
embedding dimension, vector backend, chunking strategy, schema version, and row
count. Audit warns when metadata is missing and degrades when it no longer
matches the current embedding/vector contract.

Dry-run a repair plan without mutating the DB:

```bash
python scripts/memory_audit.py --workspace <workspace_id> --repair-fts --dry-run-repair --json
```

Use the strict gate in CI/deploy pipelines:

```bash
python scripts/memory_ci_gate.py --workspace <workspace_id> --db-path .agent_memory/memory.db --vector-path .agent_memory/vectors.lance
```

To start over with a clean memory:

```bash
rm -rf .agent_memory
python scripts/bootstrap_db.py
```

## Test

```bash
pytest                  # unit + property + integration + e2e
pytest -m needs_ollama  # extraction tests against a live Ollama (opt-in)
```

## Paste-and-forget agent prompts

For the laziest possible setup, hand the agent one of two prompts and it
does the rest itself. See [`AGENT_SETUP/`](AGENT_SETUP/):

- [`01_FRESH_PROJECT.md`](AGENT_SETUP/01_FRESH_PROJECT.md) — paste in a new
  chat. Agent locates the agent-memory-lite repo, runs
  `setup_agent.py --project`, verifies MCP tools, leaves a setup-complete
  episode. No follow-up questions to you.
- [`02_CAPTURE_THIS_CHAT.md`](AGENT_SETUP/02_CAPTURE_THIS_CHAT.md) — paste
  in a chat that already has work in it. Agent ensures memory is wired,
  walks the conversation, persists task state + decisions + episodes, and
  verifies by querying back.

For the manual setup commands behind those prompts, see below.

## Make agents use this memory persistently

A one-shot prompt does not persist between sessions. Run **one command per
project** and every AI agent that opens that project will load the memory
contract, see the memory tools as native tool calls, and (for Claude Code)
get memory context auto-injected before every prompt.

### Per-project memory (recommended for multiple projects)

Each project gets its own isolated memory — no cross-project leakage.

```bash
cd /path/to/your/project
python /path/to/agent-memory-lite/scripts/setup_agent.py --project
```

Writes:
- `<project>/.claude/settings.json` — MCP server entry whose `env` pins
  `MEMORY_DB_PATH` and `VECTOR_DB_PATH` to `<project>/.agent_memory/`.
- `<project>/CLAUDE.md` and `<project>/AGENTS.md` — the agent contract
  (Claude Code reads CLAUDE.md, Codex reads AGENTS.md).
- A neutral memory bootstrap inside the DB: one memory-population skill, one
  memory-population playbook, and shared vocabulary concepts. This seed never
  writes behavior instructions, language preferences, communication style,
  personality, or project-specific roles. Skip it with
  `--no-seed-memory-bootstrap`.
- `<project>/.agent_memory/memory.db` — bootstrapped fresh.

**Project isolation works on any runtime.** The MCP server has three
ways to find the right database, in this order of precedence:

1. **`MEMORY_DB_PATH` env var** in the MCP server config — Claude Code
   project mode uses this (written by `setup_agent.py --project` into
   `<project>/.claude/settings.json`). Highest precedence.
2. **`<cwd>/.agent_memory/memory.db` auto-detect** — works on every
   runtime that spawns the MCP server with the project as cwd. Codex,
   Cursor, custom IDE plugins, anything — no per-runtime config file
   needed, just bootstrap `.agent_memory/` in each project.
3. **Defaults from `.env`** in the agent-memory-lite repo. Used when the
   MCP server is launched outside any project.

So when you open project A in any runtime, the spawned MCP server sees
only A's memory. Open project B, you get only B's. The optional HTTP
hook (Claude Code) carries the same isolation via the
`X-Memory-DB-Path` header that the project-scoped hook command sends.

### Global memory (one shared pool across all projects)

```bash
python scripts/setup_agent.py
```

Writes to `~/.claude/`, `~/.codex/`, `~/.cursor/`. Useful when you
explicitly want one memory pool everywhere on the machine. Comes with the
Claude Code `UserPromptSubmit` hook that auto-injects `<memory_context>`
before every prompt (see `scripts/inject_memory_context.py`).

The script (either mode) is idempotent. It:

1. Verifies the venv has `agent-memory-lite` + the `[mcp]` extra installed.
2. Detects Ollama (binary, daemon, `qwen2.5:7b-instruct`) and the memory db.
3. Bootstraps the database if missing.
4. Seeds neutral memory-population helpers unless `--no-seed-memory-bootstrap`
   is passed.
5. Sets `OLLAMA_PROBE_SKIP` based on whether Ollama is reachable.
6. For every agent runtime present on the machine, writes:
   - **Claude Code** (`~/.claude/`):
     `settings.json` MCP server entry + `CLAUDE.md` contract +
     `UserPromptSubmit` hook (calls `scripts/inject_memory_context.py`,
     which prepends `<memory_context>` to every user prompt).
   - **Codex** (`~/.codex/`):
     `config.toml` MCP server entry + `AGENTS.md` contract.
   - **Cursor** (`~/.cursor/`):
     `mcp.json` MCP server entry + `rules/agent-memory-lite.md` contract.
7. Emits a generic JSON snippet for any other MCP-aware agent.
8. Smoke-tests the MCP stdio server (initialize + tools/list).

After this, in any new chat the agent has three layers of "don't forget":

- **Tools layer**: base memory tools plus theory/research/capability tools
  (`memory_list_candidates`, `memory_promote_candidate`,
  `memory_reject_candidate`, `memory_write_theory`, `memory_register_snapshot`,
  `memory_write_experiment`, `memory_add_experiment_result`,
  `memory_list_research_agenda`, `memory_upsert_agent_role`,
  `memory_upsert_agent_skill`, `memory_upsert_agent_playbook`,
  `memory_list_agent_capabilities`, `memory_upsert_behavior_instruction`,
  `memory_list_behavior_instructions`, `memory_list_maintenance_events`,
  `memory_resolve_maintenance_event`, and related concept/insight tools) appear
  in the tool list natively (via MCP), no system prompt required.
- **Instructions layer**: the contract markdown is auto-loaded into the
  agent's system context every session.
- **Auto-injection layer** (Claude Code only): the hook calls the HTTP
  service for every user prompt and prepends a `<memory_context>` block,
  so the agent sees relevant memory **before** it decides whether to call
  any tools.

Re-run `python scripts/status.py` at any time to see the current state.
Use `python scripts/research_status.py --workspace <workspace_id>` to inspect
the research memory backlog. Use `python scripts/memory_hygiene.py --workspace
<workspace_id>` to inspect content-discipline gaps, and use
`python scripts/run_evals.py --workspace <workspace_id> --no-vector` for a fast
offline eval run that does not load an embedding model or vector store.

To apply only the neutral memory-population seed to an existing local DB:

```bash
python scripts/seed_project_memory.py --workspace <workspace_id> --db-path .agent_memory/memory.db --json
```

The seed is idempotent and intentionally non-behavioral. It exists only to make
future agents populate memory with the right first-class objects.

Flags:
- `--check-only` — diagnose only, no writes.
- `--no-hook` — skip the Claude Code hook (tools + contract still installed).
- `--no-seed-memory-bootstrap` skips the neutral memory-population seed.

### What you still need to start manually

The MCP stdio server boots per agent session — no separate process required
for tools to work. The **HTTP service** is what backs the auto-injection
hook and any non-MCP client. One command from the repo root:

```cmd
.\start.bat            (Windows)
./start.sh             (macOS / Linux / Git Bash)
```

The launchers auto-detect the project venv (`.venv/Scripts/python.exe` or
`.venv/bin/python`), bootstrap the DB if missing, refuse to start when
port 8765 is already taken, and run `python -m agent_memory_lite` in the
foreground (Ctrl+C to stop). Override the port with
`AGENT_MEMORY_PORT=<n>`.

To keep it running across reboots: put `.\start.bat` in a Windows startup
folder, drop `./start.sh` into a launchd plist, a systemd user service,
or whatever your OS prefers.

### The contract behind it all

`docs/AGENT_CONTRACT.md` is the canonical instruction text. Setup writes
it into each runtime's "always-loaded" file — you can edit it once and
rerun setup to push the new version everywhere.

## Workspace ingestion

Index a whole project tree (respects `.gitignore` + builtin denylist + optional
`.memoryignore`):

```bash
python scripts/ingest_workspace.py --workspace <workspace_id> --path /path/to/repo
```

## Reindex vectors

When you change the embedding model or restore from a backup that lost LanceDB:

```bash
python scripts/reindex_vectors.py
```

## Local-only enforcement

At startup, every `*_BASE_URL` setting is parsed and rejected if the host is not
`127.0.0.1` / `localhost` / `::1`, or if it matches a cloud provider denylist
(`api.openai.com`, `api.anthropic.com`, `api.cohere.com`, …).
Cloud LLM SDKs (`openai`, `anthropic`, `cohere`, …) are banned at lint time
via `ruff`'s `flake8-tidy-imports`.

To override the guard for a one-off (e.g. local development with a non-loopback host),
set both `LOCAL_ONLY=false` and `ALLOW_REMOTE_PROVIDERS=true`. **Do not** ship that
configuration.

Optional HTTP token auth can be enabled for local `/memory/*` endpoints:

```bash
echo "<local-secret>" > .agent_memory/token
export MEMORY_REQUIRE_API_TOKEN=true
export MEMORY_API_TOKEN_FILE=.agent_memory/token
python -m agent_memory_lite
```

When enabled, `/health` remains open for local monitoring, while `/memory/*`
requires `Authorization: Bearer <local-secret>`. If the token file is missing or
empty, startup fails fast instead of silently running an unprotected API.
Set `MEMORY_AUDIT_API_AUTH_FAILURES=true` to record rejected `/memory/*`
requests as `api_auth_failure` maintenance events without storing the supplied
token value.

## Project layout

See `CLAUDE.md` for the layered architecture and `docs/` for design notes.
Source files are capped at ~150 SLOC; concerns the spec collapses into one
file (`retrieval.py`, `graph.py`, `extraction.py`, `chunking.py`,
`redaction.py`) live as subpackages.

## License

MIT.
