# Prompt: capture THIS active chat into project memory

Paste this into a chat where work has already happened. The agent will
ensure memory is set up for the current project, then go back through the
conversation and persist what we did. Use it any time you want to
"snapshot" a session before closing it, or when you realize mid-chat you
forgot to wire memory.

---

You are an autonomous archivist. Your job in this turn is to make sure
agent-memory-lite is configured for the current project and then write a
faithful summary of THIS conversation into that project's memory. After
this turn, future chats in this project should be able to ask about today's
session and get accurate answers.

# Step 0 — silent failure mode

You may NOT ask the user any clarifying questions. If something is
ambiguous, choose the safest default and document it in the final report.

# Step 1 — ensure memory is set up

Follow `AGENT_SETUP/01_FRESH_PROJECT.md` Steps 1–4 exactly. If the
project's memory was missing, run setup.

If MCP tools end up still not visible after setup (because the runtime
loaded its config before setup wrote it), STOP and tell the user — name
the actual runtime hosting you (Claude Code / Codex / Cursor / "your AI
runtime" if unsure):

> Memory needs <RUNTIME_NAME> to restart to pick up the new MCP config.
> Close and reopen <RUNTIME_NAME>, then re-paste this prompt and I will
> capture the session.

Codex caveat: Codex reads MCP servers from `~/.codex/config.toml`, not
from the project directory. If the user is on Codex, recommend running
`python <REPO_ROOT>/scripts/setup_agent.py` (no `--project`) once so the
global Codex config gets the entry, then restart Codex.

Fallback path when MCP is not yet live but you still want to capture:
probe `GET http://127.0.0.1:8765/health`. If the HTTP service answers,
you can call the same operations as REST endpoints
(`POST /memory/ingest_episode`, `POST /memory/write_decision`,
`POST /memory/update_task_state`, `POST /memory/get_context`) using
`workspace_id="default"`. Otherwise stop and report — do not silently
drop the chat into the void.

# Step 2 — review THIS conversation

Scroll back through the entire conversation that has already happened in
this chat. Build an internal list of:
- **Tasks** the user gave you and whether you finished them.
- **Decisions** taken (architectural choices, library choices, "let's do
  X instead of Y", "we'll skip this for now"). Include both your decisions
  and the user's confirmations.
- **Discoveries** about the codebase or the problem (file structure,
  invariants, gotchas, surprising behavior).
- **Bugs found** and **fixes applied**.
- **Open questions** that remain.
- **Files touched**, in what way (created / edited / deleted).

If the conversation is short or there is nothing meaningful, write one
single summary episode and stop after Step 6 — do not pad.

# Step 3 — write task state

For the most recent / current task, call `memory_update_task_state`:

```json
{
  "workspace_id": "default",
  "task_id": "<short kebab-case slug from the task>",
  "goal": "<one-sentence goal>",
  "status": "in_progress | blocked | done",
  "current_plan": ["<step>", ...],
  "completed_steps": ["<step>", ...],
  "next_action": "<what should happen next, or null if done>",
  "blockers": ["<blocker>", ...],
  "files_in_scope": ["<relative paths>"]
}
```

If multiple distinct tasks happened in this chat, pick the most recent and
write its state. The others should appear as episodes (Step 5) — not every
task needs its own row.

# Step 4 — write decisions

For each architectural decision, call `memory_write_decision`. One call per
decision. Include rationale.

```json
{
  "workspace_id": "default",
  "title": "<short title>",
  "decision_text": "<one paragraph>",
  "rationale": "<why this and not alternative>",
  "confidence": 0.85,
  "importance": 0.75
}
```

If a decision in this chat clearly replaces an earlier decision in the
project's memory, first call `memory_get_context` with a relevant query to
find the earlier decision's id, then pass it as
`supersedes_decision_id`.

# Step 5 — write episodes

Walk the conversation chronologically and call `memory_ingest_episode`
for each meaningful event. Group small steps; one call per logical unit.
Aim for 5–20 episodes per chat — fewer if it was short, never more than 30.

```json
{
  "workspace_id": "default",
  "task_id": "<same slug as Step 3 if related>",
  "source_type": "agent_action",
  "raw_text": "<plain-text recap of one logical step: what you did, what worked, what surprised you>",
  "trust_level": "agent_observed",
  "importance": <0.3 to 0.8 by significance>
}
```

Important rules for raw_text:
- Self-contained. A future agent reading this episode in isolation must
  understand it without scrolling back.
- Reference file paths exactly as they appear in the project.
- Quote error messages verbatim if relevant.
- Do not redact secrets manually — the server does it.
- Do not write opinions ("this was a good idea") — write facts ("decided
  to use X because Y").

# Step 6 — verify by querying

Call `memory_get_context` with a representative query about this chat
(use a few keywords from the actual work). Confirm at least one of your
just-written episodes / decisions appears in the response.

If the query returns nothing relevant, write ONE more summary episode
that explicitly mentions the keywords, then stop. Do not loop.

# Step 7 — final report

Print exactly this structure:

```
captured this chat into <project>/.agent_memory/memory.db

task_state:    <task_id> = <status>
decisions:     <count> written  (titles: <title 1>; <title 2>; ...)
episodes:      <count> written
verification:  <ok | partial — context query returned no overlap; wrote a fallback summary>

How a future chat will find this:
- Open Claude Code in this project, ask any question that mentions the
  task or the files we touched.
- The auto-injection hook (if installed) will prepend the relevant
  context. Otherwise the agent will call memory_get_context itself.
```
