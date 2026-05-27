# Prompt: capture THIS active chat into project memory

Paste this into a chat where work has already happened. The agent will ensure
agent-memory-lite is configured for the current project, then persist a faithful
summary of this conversation into that project's memory.

---

You are an autonomous archivist. Your job in this turn is to make sure
agent-memory-lite is configured for the current project and then write a
faithful summary of THIS conversation into that project's memory. After this
turn, future chats in this project should be able to ask about today's session
and get accurate answers.

# Step 0 - silent failure mode

You may NOT ask the user clarifying questions. If something is ambiguous,
choose the safest default and document it in the final report.

# Step 1 - ensure memory is set up

Follow `AGENT_SETUP/01_FRESH_PROJECT.md` Steps 1-4 exactly. If the project's
memory was missing, run setup.

If MCP tools are still not visible after setup because the runtime loaded its
config before setup wrote it, STOP and tell the user:

> Memory needs <RUNTIME_NAME> to restart to pick up the new MCP config. Close
> and reopen <RUNTIME_NAME>, then re-paste this prompt and I will capture the
> session.

Codex caveat: Codex reads MCP servers from `~/.codex/config.toml`, not from the
project directory. If the user is on Codex, recommend running
`python <REPO_ROOT>/scripts/setup_agent.py` once without `--project`, then
restart Codex.

Fallback path when MCP is not yet live but the HTTP service answers at
`GET http://127.0.0.1:8765/health`: call the same operations as REST endpoints
using the project `WORKSPACE_ID`. Otherwise stop and report; do not silently
drop the chat into the void.

Before any memory write, determine `WORKSPACE_ID`:
- If the user or existing project instructions name a workspace id, use that
  exact value.
- Else if existing memory clearly already uses one workspace id, keep it.
- Otherwise use the project directory name.

# Step 2 - review THIS conversation

Scroll back through the entire conversation. Build an internal list of:
- Tasks the user gave you and whether you finished them.
- Decisions taken, including architectural choices and user confirmations.
- Discoveries about the codebase or problem.
- Bugs found and fixes applied.
- Open questions that remain.
- Files touched and whether each was created, edited, or deleted.

If the conversation is short or there is nothing meaningful, write one single
summary episode and stop after Step 6. Do not pad.

# Step 3 - write task state

For the most recent/current task, call `memory_write(kind="task")`:

```json
{
  "workspace_id": "<WORKSPACE_ID>",
  "task_id": "<short kebab-case slug from the task>",
  "goal": "<one-sentence goal>",
  "status": "in_progress | blocked | done",
  "current_plan": ["<step>", "..."],
  "completed_steps": ["<step>", "..."],
  "next_action": "<what should happen next, or null if done>",
  "blockers": ["<blocker>", "..."],
  "files_in_scope": ["<relative paths>"]
}
```
If multiple distinct tasks happened in this chat, pick the most recent and write
its state. The others should appear as episodes, not separate task rows.

# Step 4 - write decisions, theories, and capabilities

For each architectural decision, call `memory_write(kind="decision")`. One call per
decision, with rationale.

If the conversation formed a research hypothesis or edge theory, call
`memory_write(kind="theory")`. If the conversation used a database export or
replay dataset, record the relevant artifact in the theory payload or the task
plan. If the conversation planned or ran a research test, capture the result as
an insight or task follow-up on the compact v3 surface.

Write theories with enough discipline that a future agent can test them:
include validation criteria, expected evidence, and any decision IDs that depend
on the theory. If the conversation disproved a tempting claim, write it as a
`status="rejected"` theory with refuting evidence instead of dropping it into a
summary episode.

If the conversation clarified a reusable role, skill, or repeatable workflow,
call `memory_write(kind="skill")` with `subtype` set to `role`, `skill`, or
`playbook`. Use capability memory for operating knowledge that future agents
should retrieve before doing similar work.

If a role, skill, or playbook is specifically required to review, test, or
operate a theory, insight, candidate, or decision, record that requirement in
the task or plan step. This keeps capability context visible without relying on
removed legacy link routes.

If extraction creates review candidates, use `/memory/review_queue` or the
review UI for the same workspace and promote only candidates that are truly
supported by the conversation. Reject weak candidates so the audit trail keeps
negative evidence.

# Step 5 - write episodes

Walk the conversation chronologically and call `memory_write(kind="episode")` for each
meaningful event. Group small steps; one call per logical unit. Aim for 5-20
episodes per chat, fewer if it was short, never more than 30.

```json
{
  "workspace_id": "<WORKSPACE_ID>",
  "task_id": "<same slug as Step 3 if related>",
  "source_type": "agent_action",
  "raw_text": "<plain-text recap of one logical step: what you did, what worked, what surprised you>",
  "trust_level": "agent_observed",
  "importance": 0.6
}
```

Important rules for `raw_text`:
- Make it self-contained.
- Reference file paths exactly as they appear in the project.
- Quote error messages verbatim if relevant.
- Do not manually redact secrets; the server handles redaction.
- Do not write opinions. Write facts and evidence.

# Step 6 - verify by querying

Call `memory_brief / memory_search` with a representative query about this chat. Confirm
that at least one just-written episode, decision, theory, experiment, or insight
appears in the response. If capabilities were written, confirm the response also
contains `<agent_capabilities>` or call `memory_search(kinds=["skill"])` with
the same query.

Run a read-only retrieval integrity audit with
`scripts/memory_audit.py --workspace <WORKSPACE_ID> --json` if the local repo
is available. Report degraded checks; do not repair unless repair was part of
the user's request.

Run `scripts/memory_hygiene.py --workspace <WORKSPACE_ID> --json` when the
session produced theories, experiments, insights, candidates, decisions, roles,
skills, or playbooks. Also run
`scripts/memory_quality_gate.py --workspace <WORKSPACE_ID> --json` when the
session changed research objects or important decisions. Also run
`scripts/memory_candidate_triage.py --workspace <WORKSPACE_ID> --json` when
candidate extraction is active, and run
`scripts/memory_mcp_smoke.py --workspace <WORKSPACE_ID> --require-behavior --require-capabilities --json`
after MCP or behavior/capability changes. If the project has
`.agent_memory/retrieval_sentinels.yaml`, run
`scripts/memory_watchdog.py --workspace-id <WORKSPACE_ID> --sentinels
<PROJECT_ROOT>/.agent_memory/retrieval_sentinels.yaml --json` and report any
watchdog maintenance event id.

If the only hygiene issue is missing capability links and the report includes
`suggested_capability_links`, run
`scripts/memory_auto_triage.py --workspace <WORKSPACE_ID> --json` first. Apply
with `--apply --backup-first` only when the suggestions pass thresholds; do not
use auto-triage for stale experiments, weak theories, or unsupported insights.

If the query returns nothing relevant, write one more summary episode that
explicitly mentions the keywords, then stop. Do not loop.

# Step 7 - final report

Print exactly this structure:

```text
captured this chat into <project>/.agent_memory/memory.db

workspace_id:   <WORKSPACE_ID>
task_state:    <task_id> = <status>
decisions:     <count> written  (titles: <title 1>; <title 2>; ...)
theories:      <count> written
experiments:   <count> written
capabilities:  <roles>/<skills>/<playbooks> written
episodes:      <count> written
verification:  <ok | partial - context query returned no overlap; wrote a fallback summary>

How a future chat will find this:
- Open this project in any MCP-aware agent runtime and ask about the task or
  files we touched.
- The agent will call `memory_brief / memory_search` itself per the contract in
  `<PROJECT_ROOT>/CLAUDE.md` and `<PROJECT_ROOT>/AGENTS.md`.
```
